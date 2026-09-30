"""Local ledger paging: stable ID snapshots, preserved fields and no source calls."""
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import sys
import threading
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from activity import activity_page, activity_query, _TOTALS


class Ledger:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._mutex = threading.RLock()
        self.db.executescript("""
        CREATE TABLE requests(id INTEGER PRIMARY KEY,started REAL,finished REAL,headers TEXT,analysis TEXT,
                              kind TEXT,outcome TEXT,url TEXT,raw_ref TEXT,sha256 TEXT,purpose TEXT);
        CREATE TABLE events(id INTEGER PRIMARY KEY,created REAL,kind TEXT,message TEXT,evidence TEXT);
        """)
        self.source_calls = []

    def _get(self, key):
        assert key == "instance_id"
        return "offline-instance"

    def add(self, kind, row_id):
        with self._mutex, self.db:
            if kind == "requests":
                self.db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (row_id, 1000.25, 1001.5, json.dumps({"content-type": "text/html"}),
                                 json.dumps({"rows": 80}), "list", "real_data", "https://example.invalid/list",
                                 "raw/response.body", "abc", "forward"))
            else:
                self.db.execute("INSERT INTO events VALUES(?,?,?,?,?)",
                                (row_id, 1000.25, "paused", "人工暂停", json.dumps({"请求": row_id})))


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.engine = Ledger()

    def tearDown(self):
        self.engine.db.close()

    def test_snapshot_keyset_survives_new_inserts_without_gaps_or_duplicates(self):
        for row_id in [1, 3, 7, 11, 14, 20, 21]:
            self.engine.add("requests", row_id)
        first = activity_page(self.engine, "requests", limit=3)
        self.assertEqual([r["id"] for r in first["items"]], [21, 20, 14])
        self.assertEqual((first["snapshot_id"], first["next_cursor"], first["total"]), (21, 14, 7))
        self.engine.add("requests", 99)
        second = activity_page(self.engine, "requests", limit=3, before_id=first["next_cursor"], snapshot_id=first["snapshot_id"])
        third = activity_page(self.engine, "requests", limit=3, before_id=second["next_cursor"], snapshot_id=first["snapshot_id"])
        self.assertEqual([r["id"] for r in second["items"]], [11, 7, 3])
        self.assertEqual([r["id"] for r in third["items"]], [1])
        self.assertEqual(second["total"], 7)
        self.assertEqual(third["total"], 7)
        self.assertFalse(third["has_more"])
        self.assertIsNone(third["next_cursor"])
        newest = activity_page(self.engine, "requests", limit=3)
        self.assertEqual(newest["items"][0]["id"], 99)
        self.assertEqual(newest["total"], 8)
        self.assertEqual(self.engine.source_calls, [])

    def test_events_have_independent_snapshot_and_preserve_json_fields(self):
        for row_id in range(1, 5):
            self.engine.add("events", row_id)
        first = activity_page(self.engine, "events", limit=2)
        self.engine.add("events", 5)
        second = activity_page(self.engine, "events", limit=2, before_id=first["next_cursor"], snapshot_id=first["snapshot_id"])
        self.assertEqual([r["id"] for r in first["items"] + second["items"]], [4, 3, 2, 1])
        self.assertEqual(first["items"][0]["message"], "人工暂停")
        self.assertEqual(first["items"][0]["evidence"], {"请求": 4})
        self.assertEqual(first["items"][0]["created_at"], "1970-01-01T00:16:40.250000+00:00")
        self.assertEqual(first["items"][0]["instance_id"], "offline-instance")
        self.assertEqual(first["total"], 4)
        self.assertFalse(second["has_more"])

    def test_request_fields_match_existing_serializer_and_empty_page_is_explicit(self):
        empty = activity_page(self.engine, "requests")
        self.assertEqual(empty, {"items": [], "has_more": False, "next_cursor": None, "snapshot_id": 0, "total": 0})
        self.engine.add("requests", 1)
        item = activity_page(self.engine, "requests")["items"][0]
        self.assertEqual(item["headers"], {"content-type": "text/html"})
        self.assertEqual(item["analysis"], {"rows": 80})
        self.assertEqual(item["raw_ref"], "raw/response.body")
        self.assertEqual(item["started_at"], "1970-01-01T00:16:40.250000+00:00")
        self.assertEqual(item["finished_at"], "1970-01-01T00:16:41.500000+00:00")
        self.assertEqual(item["instance_id"], "offline-instance")

    def test_limits_and_cursor_validation_reject_ambiguous_or_unbindable_ids(self):
        for kwargs in ({"limit": 0}, {"limit": 201}, {"limit": True}, {"before_id": -1},
                       {"snapshot_id": 0}, {"before_id": "1"}, {"before_id": 2**63}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                activity_page(self.engine, "requests", **kwargs)
        with self.assertRaises(ValueError):
            activity_page(self.engine, "tasks")
        with self.assertRaises(ValueError):
            activity_page(self.engine, "requests", snapshot_id=100)
        for query in ({"paged": ["0"]}, {"paged": ["1"], "before_id": [""]},
                      {"paged": ["1"], "before_id": ["1", "2"]}, {"paged": ["1"], "before_id": ["+1"]},
                      {"paged": ["1"], "snapshot_id": ["1.0"]}, {"paged": ["1"], "limit": ["201"]}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                activity_query(query)
        self.assertEqual(activity_query({"paged": ["1"], "before_id": ["4"], "snapshot_id": ["8"]}),
                         {"limit": 30, "before_id": 4, "snapshot_id": 8})

    def test_snapshot_total_cache_avoids_repeat_full_counts_and_stays_bounded(self):
        for row_id in range(1, 5):
            self.engine.add("requests", row_id)
        sql = []
        self.engine.db.set_trace_callback(sql.append)
        self.assertEqual(activity_page(self.engine, "requests", limit=2)["total"], 4)
        self.assertEqual(activity_page(self.engine, "requests", limit=2, before_id=3, snapshot_id=4)["total"], 4)
        self.engine.add("requests", 5)
        self.assertEqual(activity_page(self.engine, "requests")["total"], 5)
        count_queries = [q for q in sql if q.startswith("SELECT COUNT(*)")]
        self.assertEqual(len(count_queries), 2)
        self.assertIn("id>4 AND id<=5", count_queries[-1])
        for row_id in range(6, 50):
            self.engine.add("requests", row_id)
            self.assertEqual(activity_page(self.engine, "requests")["total"], row_id)
        self.assertLessEqual(len(_TOTALS[self.engine]["requests"]), 32)
        # An evicted older snapshot remains exact using an indexed difference.
        self.assertEqual(activity_page(self.engine, "requests", snapshot_id=3)["total"], 3)


class MobileActivityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed only for the offline UI regression")
    def test_independent_history_pages_and_bounded_dom(self):
        # Real app.js + lightweight DOM/fetch fixtures: no browser or source calls.
        script = r"""
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const path=process.argv[2]+'/';
const html=fs.readFileSync(path+'index.html','utf8'), source=fs.readFileSync(path+'app.js','utf8');
class Node{constructor(){this.children=[];this.textContent='';this.value='';this.hidden=false;this.disabled=false;this.dataset={};this.style={};this.listeners={};this.attrs={};this.classList={toggle(){},contains(){return false;}};}append(...n){this.children.push(...n);}replaceChildren(...n){this.children=n;}setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}addEventListener(k,f){(this.listeners[k]||=[]).push(f);}focus(){}scrollIntoView(){}showModal(){this.open=true;}}
const ids=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Node()]));
const document={baseURI:'http://localhost/collector/',hidden:false,getElementById:k=>ids[k],createElement:()=>new Node(),querySelector:()=>new Node(),querySelectorAll:()=>[],addEventListener(){}};
const window={addEventListener(){}};
const records={requests:Array.from({length:135},(_,i)=>({id:i+1,kind:'list',stock:'601012',page:i+1,outcome:'real_data',started_at:'2026-10-01T00:00:00Z'})),events:Array.from({length:47},(_,i)=>({id:i+1,kind:'paused',message:'人工暂停',created_at:'2026-10-01T00:00:00Z'}))};
const config={stocks:['601012'],from_date:'2025-10-01',to_date:'2026-10-01',interval_seconds:60,client:'curl'};
const status={state:'paused',job:{id:1,config},config,aggregate:{attempts:135,list_pages:135,calibration_pages:0,calibration_requests:0,unique_posts:10,list_only:6,body_complete:3,detail_required:4,pending:1},coverage:[{stock:'601012',pages:135,details:{required:4,complete:3,pending:1,list_only:6},gaps:[]}],current:{kind:'list',stock:'601012',page:136},server_time:'2026-10-01T00:00:00Z'};
const calls=[];
async function fetch(url,opt={}){const u=new URL(url),name=u.pathname.replace('/collector/api/','');calls.push({name,method:opt.method||'GET',query:u.search});let data;if(name==='session')data={authenticated:false};else if(name==='status')data=status;else if(name==='jobs')data=[];else if(records[name]){assert.strictEqual(u.searchParams.get('paged'),'1');const snapshot=Number(u.searchParams.get('snapshot_id')||Math.max(...records[name].map(r=>r.id)));const before=Number(u.searchParams.get('before_id')||Infinity);const limit=Number(u.searchParams.get('limit'));const all=records[name].filter(r=>r.id<=snapshot).sort((a,b)=>b.id-a.id);const rows=all.filter(r=>r.id<before);const items=rows.slice(0,limit);data={items,snapshot_id:snapshot,total:all.length,has_more:rows.length>limit,next_cursor:rows.length>limit?items.at(-1).id:null};}else throw Error(name);return{ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>structuredClone(data)};}
const script=source.replace(/\}\)\(\);\s*$/,`window.test={load(s){authenticated=true;online=true;renderStatus(s);},poll,loadActivity,getPages(){return activityPages;}};})();`);
vm.runInNewContext(script,{document,window,fetch,URL,Intl,Date,Promise,JSON,Number,String,Object,Array,Math,RegExp,Error,setInterval:()=>0,clearInterval(){},setTimeout:f=>f()});
(async()=>{await new Promise(setImmediate);const t=window.test;t.load(status);await t.poll();assert.strictEqual(ids['request-list'].children.length,30);assert.strictEqual(ids['event-list'].children.length,20);assert.strictEqual(ids['metric-list-only'].textContent,'6');
  const bar=ids['coverage-list'].children[0].children.find(n=>n.attrs.role==='progressbar');assert.strictEqual(bar.attrs['aria-valuenow'],'75','body denominator excludes list-only');
  await t.loadActivity('requests','next');const a=t.getPages().requests;assert.strictEqual(a.index,1);assert.strictEqual(a.page.items[0].id,105);assert.strictEqual(a.snapshot,135);
  records.requests.push({...records.requests[0],id:136});records.events.push({...records.events[0],id:48});status.aggregate.attempts=136;const reads=calls.filter(c=>c.name==='requests').length;const firstRow=ids['request-list'].children[0];await t.poll();assert.strictEqual(calls.filter(c=>c.name==='requests').length,reads);assert.strictEqual(ids['request-list'].children[0],firstRow,'historical DOM survives polls');assert.strictEqual(a.index,1);assert.strictEqual(a.page.total,135);assert.strictEqual(ids['metric-attempts'].textContent,'136');assert.strictEqual(t.getPages().events.page.total,48);
  await t.loadActivity('events','next');const eventFirst=ids['event-list'].children[0];await t.poll();assert.strictEqual(ids['event-list'].children[0],eventFirst,'events retain independent history');
  await t.loadActivity('requests','prev');assert.strictEqual(a.index,0);assert.strictEqual(a.latest,false);assert.strictEqual(a.page.items[0].id,135);await t.poll();assert.strictEqual(a.page.items[0].id,135,'first historical snapshot does not auto-jump to latest');
  await t.loadActivity('requests','latest');assert.strictEqual(a.page.items[0].id,136);assert.strictEqual(a.latest,true);assert.strictEqual(t.getPages().events.latest,false);
  while(a.page.has_more){await t.loadActivity('requests','next');assert(ids['request-list'].children.length<=30);}assert.strictEqual(ids['requests-next'].disabled,true);assert.strictEqual(a.page.total,136);assert.strictEqual(a.index,4);
  assert(!calls.some(c=>c.method!=='GET'),'paging never issues controls/source mutations');console.log('PASS: independent keyset navigation, fixed historical snapshot, status polling without replacing rows, latest refresh, <=30/20 DOM rows, list-only/body separation with 75% denominator, no source controls.');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        with tempfile.TemporaryDirectory() as temp:
            runner = Path(temp) / "paging-check.cjs"
            runner.write_text(script)
            result = subprocess.run([shutil.which("node"), str(runner), str(APP / "static")],
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: independent keyset navigation", result.stdout)


if __name__ == "__main__":
    unittest.main()
