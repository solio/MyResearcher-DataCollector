import json
from datetime import datetime, timezone

from myresearcher_collector.detail_enrichment import execute_detail_enrichment
from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.sources.eastmoney_guba.acquisition import AcquiredDocument, BROWSER_DOM_SNAPSHOT


def _detail(item_id="1"):
    payload={"post_id":int(item_id),"post_user":{"user_id":"u","user_nickname":"n"},
      "post_guba":{"stockbar_code":"601012","stockbar_name":"x"},"post_title":"full",
      "post_content":"完整正文内容","post_publish_time":"2026-08-01 10:00:00",
      "post_type":0,"post_state":0,"post_top_status":0,"post_click_count":1,
      "post_comment_count":2,"post_like_count":3,"post_forward_count":4}
    return f"<script>var post_article={json.dumps(payload)};</script>".encode()


class T:
    def get(self,url,*,timeout):
        return AcquiredDocument(_detail(),url,url,BROWSER_DOM_SNAPSHOT,datetime.now(timezone.utc),None,None,{})


def test_enrichment_updates_same_row_and_skips_non_candidates(tmp_path):
    store=SimplePostStore(tmp_path/"collector.db")
    store.upsert_post(source="eastmoney_guba",source_item_id="1",stock_code="601012",title="x"*40,content=None,author_id="u",author_name="n",published_at="2026-08-01T02:00:00.000000Z",url="https://guba.eastmoney.com/news,601012,1.html",read_count=0,reply_count=0,like_count=0,forward_count=0)
    store.close()
    report=execute_detail_enrichment(db_path=tmp_path/"collector.db",stock_code="601012",transport=T(),sleep_fn=lambda _:None)
    assert report["success"]==1 and report["candidates_remaining"]==0
    reopened=SimplePostStore(tmp_path/"collector.db")
    assert reopened.count("eastmoney_guba","601012")==1
    assert reopened.rows("eastmoney_guba","601012")[0]["content"]=="完整正文内容"
    reopened.close()
