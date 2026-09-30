"""Exercise the real mobile console offline: node isolation and explicit controls."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]

HARNESS = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert'),crypto=require('crypto');
const path=process.argv[2]+'/';
const html=fs.readFileSync(path+'index.html','utf8'),source=fs.readFileSync(path+'app.js','utf8');
class Element {
  constructor(){this.children=[];this.textContent='';this.value='';this.hidden=false;this.disabled=false;this.open=false;this.returnValue='';this.dataset={};this.style={};this.listeners={};this.attrs={};this.classList={toggle(){},contains(){return false;}};}
  append(...n){this.children.push(...n);}replaceChildren(...n){this.children=n;}
  setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}
  addEventListener(k,f){(this.listeners[k]||=[]).push(f);}focus(){}scrollIntoView(){}
  showModal(){this.open=true;this.returnValue='';}close(v){if(v!==undefined)this.returnValue=v;this.open=false;for(const f of this.listeners.close||[])f({});}
}
const ids=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const descend=n=>[n,...n.children.flatMap(descend)];
const document={baseURI:'http://localhost/collector/',hidden:false,getElementById:k=>ids[k],createElement:()=>new Element(),querySelector:()=>new Element(),querySelectorAll(selector){const key=selector.match(/^\[data-(.+)\]$/)?.[1]?.replace(/-([a-z])/g,(_,s)=>s.toUpperCase());return key?Object.values(ids).flatMap(descend).filter(n=>n.dataset[key]!==undefined):[];},addEventListener(){}};
const window={addEventListener(){}};
const config={stocks:['601012'],from_date:'2025-10-01',to_date:'2026-10-01',interval_seconds:60,client:'curl'};
function fixture(node,count){return {instance_id:node+'-uuid',state:'paused',job:{id:1,config},config,aggregate:{attempts:65,list_pages:65,calibration_pages:0,calibration_requests:0,unique_posts:count,list_only:6,body_complete:3,detail_required:4,pending:1},coverage:[{stock:'601012',pages:65,details:{required:4,complete:3,pending:1,list_only:6},gaps:[]}],current:{kind:'list',stock:'601012',page:66},server_time:'2026-10-01T00:00:00Z'};}
const statuses={local:fixture('local',10),alpha:fixture('alpha',20),beta:fixture('beta',30)};
const records=Object.fromEntries(Object.keys(statuses).map(node=>[node,{requests:Array.from({length:65},(_,i)=>({id:i+1,kind:'list',stock:'601012',page:i+1,outcome:'real_data',started_at:'2026-10-01T00:00:00Z',instance_id:node+'-uuid'})),events:Array.from({length:42},(_,i)=>({id:i+1,kind:'paused',message:node+'人工暂停',created_at:'2026-10-01T00:00:00Z'}))}]));
const fleet={nodes:Object.keys(statuses).map(id=>({id,alias:id,name:id==='local'?'本机':id,local:id==='local',base_url:'https://'+id+'.invalid/collector/',instance_id:id+'-uuid',connection:id==='beta'?'offline':'online',status:statuses[id],last_error:id==='beta'?'连接超时':null,last_seen_at:'2026-10-01T00:00:00Z',sync:{state:id==='beta'?'error':'ready',cursor:5,error:id==='beta'?'暂未同步':null}})),merge:{unique_posts:25,body_complete:8,conflicts:1,observations:38,instances:2,cursors:{'local-uuid':5,'alpha-uuid':5},db_path:'/isolated/fleet/collector.db'}};
const calls=[],holds=[],failures=new Map();
function response(data,code=200){return {ok:code>=200&&code<300,status:code,headers:{get:()=> 'application/json'},json:async()=>structuredClone(data)};}
function deferred(predicate){let resolve;const promise=new Promise(r=>resolve=r);const hold={predicate,promise,resolve,used:false};holds.push(hold);return hold;}
async function fetch(url,opt={}){
  const u=new URL(url),name=u.pathname.replace('/collector/api/',''),method=opt.method||'GET';
  const call={name,method,query:u.search,payload:opt.body?JSON.parse(opt.body):undefined};calls.push(call);
  const hold=holds.find(h=>!h.used&&h.predicate(call));if(hold){hold.used=true;return await hold.promise;}
  const failure=failures.get(name);if(failure)return response({error:failure.error,ambiguous:failure.ambiguous===true},failure.code);
  let data;if(name==='session')data={authenticated:false};else if(name==='fleet')data=fleet;
  else if(name.startsWith('fleet/'))data=name==='fleet/sync'?{scheduled:['local','alpha']}:{id:call.payload?.id||name.split('/').at(-1)};
  else {
    const parts=name.split('/');const node=parts[0]==='nodes'?parts[1]:'local',route=parts[0]==='nodes'?parts.slice(2).join('/'):name;
    assert(statuses[node],name);
    if(route==='status')data=statuses[node];else if(route==='jobs')data=[];
    else if(route==='control'){if(call.payload?.action==='pause')statuses[node].state='paused';data=statuses[node];}
    else if(route==='jobs/current'){data=statuses[node];}
    else if(records[node][route]){
      assert.strictEqual(u.searchParams.get('paged'),'1');
      const snapshot=Number(u.searchParams.get('snapshot_id')||Math.max(...records[node][route].map(r=>r.id))),before=Number(u.searchParams.get('before_id')||Infinity),limit=Number(u.searchParams.get('limit'));
      const all=records[node][route].filter(r=>r.id<=snapshot).sort((a,b)=>b.id-a.id),rows=all.filter(r=>r.id<before),items=rows.slice(0,limit);
      data={items,snapshot_id:snapshot,total:all.length,has_more:rows.length>limit,next_cursor:rows.length>limit?items.at(-1).id:null};
    }else throw Error('Unexpected route '+name);
  }return response(data);
}
const instrumented=source.replace('throw err;', 'window.lastApiError=err; throw err;').replace(/\}\)\(\);\s*$/,`window.test={load(s){authenticated=true;online=true;renderStatus(s);},setFleet(d){fleetData=d;renderFleet(d);},api,poll,switchNode,requestSelection,loadActivity,post,openNodeForm,refreshFleet,fleetMutation,confirmNodeRemoval,markDirty,get(){return{selectedNode,nodeEpoch,status,busy,authenticated,online,formDirty,pages:activityPages,fleetData};}};})();`);
vm.runInNewContext(instrumented,{document,window,fetch,URL,Intl,Date,Promise,JSON,Number,String,Object,Array,Math,RegExp,Error,setInterval:()=>0,clearInterval(){},setTimeout:f=>f()});
const tick=()=>new Promise(setImmediate);
async function ready(){await tick();const t=window.test;t.load(statuses.local);t.setFleet(fleet);await t.poll();return t;}
async function trigger(id,type,event={preventDefault(){}}){await Promise.all((ids[id].listeners[type]||[]).map(f=>f(event)));await tick();}
function text(n){return n.textContent+n.children.map(text).join(' ');}
"""


@unittest.skipUnless(shutil.which("node"), "Node is needed only for the offline UI regression")
class FleetUITests(unittest.TestCase):
    def run_ui(self, scenario):
        script = HARNESS + "\n(async()=>{\n" + scenario + "\nconsole.log('PASS');})().catch(e=>{console.error(e);process.exitCode=1;});\n"
        with tempfile.TemporaryDirectory() as temp:
            runner = Path(temp) / "fleet-check.cjs"
            runner.write_text(script)
            result = subprocess.run([shutil.which("node"), str(runner), str(APP / "static")],
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_status_switch_ignores_old_inflight_response(self):
        self.run_ui(r"""
const t=await ready(),hold=deferred(c=>c.name==='status');
const stale=t.poll();await tick();assert(hold.used);
await t.switchNode('alpha');assert.strictEqual(t.get().selectedNode,'alpha');assert.strictEqual(ids['metric-posts'].textContent,'20');
hold.resolve(response({...statuses.local,aggregate:{...statuses.local.aggregate,unique_posts:999}}));await stale;
assert.strictEqual(t.get().status.instance_id,'alpha-uuid');assert.strictEqual(ids['metric-posts'].textContent,'20');
assert.strictEqual(ids['request-list'].children.length,30);assert.strictEqual(ids['event-list'].children.length,20);
assert(!calls.some(c=>c.method!=='GET'),'selection does not issue source controls');
""")

    def test_history_switch_resets_cursor_and_rejects_old_page(self):
        self.run_ui(r"""
const t=await ready();await t.switchNode('alpha');
const hold=deferred(c=>c.name==='nodes/alpha/requests'&&c.query.includes('before_id='));
const stale=t.loadActivity('requests','next');await tick();assert(hold.used);
await t.switchNode('local');const localFirst=ids['request-list'].children[0];
hold.resolve(response({items:[{id:1,kind:'list',stock:'STALE',outcome:'real_data'}],snapshot_id:65,total:65,has_more:false,next_cursor:null}));await stale;
assert.strictEqual(ids['request-list'].children[0],localFirst);assert(!text(ids['request-list']).includes('STALE'));
assert.strictEqual(t.get().pages.requests.index,0);assert.strictEqual(t.get().pages.requests.latest,true);
while(t.get().pages.requests.page.has_more){await t.loadActivity('requests','next');assert(ids['request-list'].children.length<=30);}
await t.loadActivity('events','next');const historicalFirst=ids['event-list'].children[0];await t.poll();assert.strictEqual(ids['event-list'].children[0],historicalFirst);assert(ids['event-list'].children.length<=20);
assert(!calls.some(c=>c.method!=='GET'));
""")

    def test_mutation_locks_selection_and_pins_pause_and_edit_target(self):
        self.run_ui(r"""
const t=await ready();await t.switchNode('alpha');statuses.alpha.state='running';t.load(statuses.alpha);
const hold=deferred(c=>c.name==='nodes/alpha/control'&&c.method==='POST');
const change=t.post('jobs/current',{...config,interval_seconds:90},'已保存','PATCH',{pauseFirst:true,expectedJobId:1});await tick();
assert(hold.used);assert(t.get().busy);assert(ids['selected-node'].disabled);assert.strictEqual(await t.switchNode('beta'),false);t.requestSelection('beta');assert.strictEqual(t.get().selectedNode,'alpha');
statuses.alpha.state='paused';hold.resolve(response(statuses.alpha));await change;
const mutations=calls.filter(c=>c.method!=='GET');assert.strictEqual(mutations.length,2);assert.strictEqual(mutations[0].name,'nodes/alpha/control');assert.strictEqual(mutations[0].payload.action,'pause');assert.strictEqual(mutations[1].name,'nodes/alpha/jobs/current');assert.strictEqual(mutations[1].method,'PATCH');
assert(!mutations.some(c=>c.payload?.action==='start'),'edit never starts acquisition');
await t.api('session');await t.api('fleet');assert.strictEqual(calls.at(-2).name,'session');assert.strictEqual(calls.at(-1).name,'fleet');
""")

    def test_dirty_switch_requires_discard_and_resets_draft(self):
        self.run_ui(r"""
const t=await ready();ids.stocks.value='600519';t.markDirty();t.requestSelection('alpha');assert(ids['switch-node-dialog'].open);assert.strictEqual(t.get().selectedNode,'local');
ids['switch-node-dialog'].close('cancel');await tick();assert.strictEqual(t.get().selectedNode,'local');assert(t.get().formDirty);
t.requestSelection('alpha');ids['switch-node-dialog'].close('confirm');await tick();await tick();assert.strictEqual(t.get().selectedNode,'alpha');assert.strictEqual(t.get().formDirty,false);assert.strictEqual(ids.stocks.value,'601012');assert(!calls.some(c=>c.method!=='GET'));
""")

    def test_tokens_are_write_only_omitted_when_unchanged_and_cleared(self):
        self.run_ui(r"""
const t=await ready(),node=fleet.nodes.find(n=>n.id==='alpha');
t.openNodeForm(node);assert.strictEqual(ids['node-token'].value,'');assert.strictEqual(ids['node-token'].required,false);
await trigger('node-form','submit');const unchanged=calls.find(c=>c.method==='PATCH');assert.strictEqual(unchanged.name,'fleet/nodes/alpha');assert(!Object.hasOwn(unchanged.payload,'token'));assert.strictEqual(ids['node-token'].value,'');
const secret=crypto.randomBytes(24).toString('hex');t.openNodeForm(node);ids['node-token'].value=secret;failures.set('fleet/nodes/alpha',{code:502,error:'意外错误 '+secret});await trigger('node-form','submit');
assert.strictEqual(ids['node-token'].value,'');assert(!text(ids['node-form-error']).includes(secret));assert(!text(ids.notice).includes(secret));assert(text(ids['node-form-error']).includes('[已隐藏密钥]'));
failures.delete('fleet/nodes/alpha');t.openNodeForm(node);ids['node-token'].value=secret;await trigger('cancel-node','click');assert.strictEqual(ids['node-token'].value,'');assert(!ids['node-dialog'].open);
assert(!source.includes('localStorage'));assert(!calls.some(c=>c.name.includes('control')));
""")

    def test_registration_sync_and_removal_never_issue_source_controls(self):
        self.run_ui(r"""
const t=await ready();t.openNodeForm();ids['node-id'].value='gamma';ids['node-name'].value='备用实例';ids['node-url'].value='https://gamma.invalid/collector/';ids['node-token'].value=crypto.randomBytes(24).toString('hex');await trigger('node-form','submit');assert.strictEqual(ids['node-token'].value,'');
await trigger('sync-fleet','click');await tick();
await t.switchNode('alpha');t.confirmNodeRemoval(fleet.nodes.find(n=>n.id==='alpha'));ids['remove-node-dialog'].close('confirm');await tick();await tick();await tick();
assert.strictEqual(t.get().selectedNode,'local');assert(!calls.some(c=>c.name.includes('control')));
const mutations=calls.filter(c=>c.method!=='GET');assert.deepStrictEqual(mutations.map(c=>c.name),['fleet/nodes','fleet/sync','fleet/nodes/alpha']);assert.deepStrictEqual(mutations[1].payload,{node_id:'all'});assert.strictEqual(mutations[2].method,'DELETE');
assert.strictEqual(ids['fleet-nodes'].children.length,3,'overview DOM bounded to registered nodes');
""")

    def test_remote_error_preserves_hub_session_and_other_nodes(self):
        self.run_ui(r"""
const t=await ready(),cards=ids['fleet-nodes'].children;
failures.set('nodes/beta/status',{code:502,error:'远端认证失败'});await t.switchNode('beta');assert(t.get().authenticated);assert.strictEqual(t.get().online,false);assert.strictEqual(ids['fleet-nodes'].children.length,3);assert(text(ids['fleet-nodes']).includes('alpha'));assert(text(ids.notice).includes('其他实例仍可查看'));assert(!ids['add-node'].disabled);
failures.set('fleet',{code:502,error:'概览暂不可读'});const fleetBefore=t.get().fleetData;await t.refreshFleet();assert.strictEqual(t.get().fleetData,fleetBefore);assert.strictEqual(ids['fleet-nodes'].children.length,3);assert(t.get().authenticated);
failures.delete('fleet');await t.switchNode('alpha');assert.strictEqual(ids['metric-posts'].textContent,'20');assert(t.get().authenticated);
failures.set('nodes/alpha/control',{code:502,error:'远端命令响应超时',ambiguous:true});const before=calls.filter(c=>c.name==='nodes/alpha/control').length;
assert.strictEqual(await t.post('control',{action:'pause'},'已暂停'),false);assert(t.get().authenticated);assert(ids.notice.textContent.includes('该实例可能已执行操作，结果尚未确认；请先刷新状态再决定，系统不会自动重发。'));assert.strictEqual(calls.filter(c=>c.name==='nodes/alpha/control').length-before,1,'ambiguous command must not automatically repeat');
assert.strictEqual(window.lastApiError.ambiguous,true);assert.strictEqual(window.lastApiError.status,502);assert(t.get().authenticated);
""")

    def test_merge_reports_manual_sync_and_recovery_errors_without_html(self):
        self.run_ui(r"""
const t=await ready();fleet.auto_sync=false;fleet.merge.recovery_errors={'alpha-uuid':{kind:'local_merge_recovery_error',error:'<script>broken raw</script>',cursor:5}};t.setFleet(fleet);
assert(ids['merge-sync-note'].textContent.includes('自动同步已关闭'));assert(!ids['merge-sync-note'].textContent.includes('每 60 秒'));assert(ids['merge-status'].textContent.includes('自动同步已关闭'));assert.strictEqual(ids['merge-error'].hidden,false);assert(ids['merge-error'].textContent.includes('<script>broken raw</script>'));assert.strictEqual(ids['merge-error'].children.length,0,'error strings remain text');assert(ids['merge-error'].textContent.includes('其他实例可继续同步'));
assert.strictEqual(ids['merge-posts'].textContent,'25');assert.strictEqual(ids['merge-bodies'].textContent,'8');assert.strictEqual(ids['merge-conflicts'].textContent,'1');
""")


if __name__ == "__main__":
    unittest.main()
