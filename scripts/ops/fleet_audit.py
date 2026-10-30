import json, sqlite3, urllib.request, sys
D="/usr/local/src/MyResearcher-DataCollector/apps/http_backfill/data"
reg=json.load(open(D+"/fleet/registry.json"))
local_tok=open(D+"/console.token").read().strip()
def api(base, tok, path):
    req=urllib.request.Request(base.rstrip("/")+path, headers={"Authorization":"Bearer "+tok})
    with urllib.request.urlopen(req, timeout=20) as r: return json.loads(r.read().decode())
def snap(base, tok):
    return api(base, tok, "/api/federation/export?after=0&limit=1")["snapshot"]
nodes=[("local","http://127.0.0.1:8790",local_tok)]
for n in reg["nodes"]:
    nodes.append((n["id"], n["base_url"], n["token"]))
fleet=api("http://127.0.0.1:8790", local_tok, "/api/fleet")
iid_by_id={n["id"]: (n.get("instance_id") or (n.get("sync",{}).get("summary",{}) or {}).get("instance_id")) for n in fleet["nodes"]}
c=sqlite3.connect("file:"+D+"/fleet/merge.sqlite3?mode=ro", uri=True)
cur=dict(c.execute("SELECT instance_id, seq FROM cursors"))
print("  节点          已消费       末端     待合并")
total=0; bad=[]
for nid, base, tok in nodes:
    iid=iid_by_id.get(nid)
    s=snap(base, tok); k=cur.get(iid, 0)
    backlog=s-k
    if backlog < 0: bad.append(nid)
    total += max(backlog,0)
    print("  " + nid.ljust(12) + str(f"{k:>10,}") + str(f"{s:>10,}") + str(f"{backlog:>10,}"))
print()
print("  合计待合并:", f"{total:,}", "条观测")
print("  断言 cursors<=snapshot:", "通过 ✓" if not bad else "失败 ✗ " + str(bad))
sys.exit(0 if not bad else 1)
