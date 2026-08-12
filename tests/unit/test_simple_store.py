from myresearcher_collector.simple_store import SimplePostStore

def _post(store, *, content=None, reply_count=1):
    return store.upsert_post(source="eastmoney_guba", source_item_id="p1", stock_code="601012", title="title", content=content, author_id="a", author_name="name", published_at="2025-01-01T00:00:00.000000Z", url="https://guba.eastmoney.com/news,601012,1.html", read_count=2, reply_count=reply_count, like_count=3, forward_count=4)

def test_insert_then_upsert_is_one_row(tmp_path):
    store=SimplePostStore(tmp_path/"collector.db")
    try:
        assert _post(store) is True; assert _post(store, reply_count=9) is False
        assert store.count("eastmoney_guba","601012")==1
        assert store.rows("eastmoney_guba","601012")[0]["reply_count"]==9
    finally: store.close()

def test_detail_updates_same_post(tmp_path):
    store=SimplePostStore(tmp_path/"collector.db")
    try:
        _post(store); assert store.update_content("eastmoney_guba","p1","full detail")
        assert store.rows("eastmoney_guba","601012")[0]["content"]=="full detail"
        assert store.count("eastmoney_guba","601012")==1
    finally: store.close()
