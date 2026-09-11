import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from myresearcher_collector.detail_enrichment import execute_detail_enrichment
from myresearcher_collector.models import GubaSourceItem
from myresearcher_collector.simple_store import SimplePostStore
from myresearcher_collector.sources.eastmoney_guba.acquisition import AcquiredDocument, BROWSER_DOM_SNAPSHOT
from myresearcher_collector.sources.eastmoney_guba.content_rules import list_title_metadata
from myresearcher_collector.sources.eastmoney_guba.parser import SCHEMA_VERSION, SOURCE
from myresearcher_collector.storage import RawEvidenceStore, SQLitePersistence


FIXTURES = Path(__file__).parents[1] / "fixtures" / "eastmoney_guba"
NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)
PUBLISHED = datetime(2026, 8, 1, 2, tzinfo=timezone.utc)
TRUNCATED_TITLE = "截" * 40
DETAIL_URL = "https://guba.eastmoney.com/news,601012,1754555652.html"


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


class BlockThenPass:
    def __init__(self): self.calls = 0
    def get(self, url, *, timeout):
        self.calls += 1
        if self.calls == 1:
            return AcquiredDocument("<title>身份核实</title><script>fd_guba_validate</script>".encode(), url, url, BROWSER_DOM_SNAPSHOT, datetime.now(timezone.utc), None, None, {})
        return T().get(url, timeout=timeout)
    def current_document(self):
        return T().get("https://guba.eastmoney.com/news,601012,1.html", timeout=1)


class FixtureTransport:
    def __init__(self, fixture_name):
        self.payload = (FIXTURES / fixture_name).read_bytes()
        self.calls = []

    def get(self, url, *, timeout):
        self.calls.append((url, timeout))
        return AcquiredDocument(
            self.payload, url, url, BROWSER_DOM_SNAPSHOT, NOW, None,
            "text/html", {},
        )


def _seed_canonical_list_observation(tmp_path, *, title=TRUNCATED_TITLE):
    raw = RawEvidenceStore(tmp_path, SOURCE)
    store = SQLitePersistence(tmp_path / "collector.db", raw)
    store.start_run(
        "seed-list", SOURCE, "stock:601012", started_at=NOW,
        collector_version="test", parser_version=SCHEMA_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    store.record_attempt(
        "seed-list", "seed-attempt", ordinal=0, request_kind="list",
        request_url="https://guba.eastmoney.com/list,601012,f.html",
        started_at=NOW, finished_at=NOW, outcome="success", retry_number=1,
        retry_budget=1, http_status=200,
    )
    published = raw.publish("seed-list", 0, b"fixture list evidence")
    store.record_raw_evidence(
        "seed-list", "seed-attempt", "seed-evidence", published,
        evidence_kind="list:http_response",
        request_url="https://guba.eastmoney.com/list,601012,f.html",
        final_url="https://guba.eastmoney.com/list,601012,f.html",
        fetched_at=NOW, http_status=200, content_type="text/html",
    )
    item = GubaSourceItem(
        source=SOURCE, schema_version=SCHEMA_VERSION,
        source_item_id="1754555652", requested_bar_code="601012",
        canonical_bar_code="601012", canonical_bar_name="x",
        author_id="u", author_name="n", title=title, content=title,
        published_at=PUBLISHED, last_updated_at=None, display_time=None,
        url=DETAIL_URL, post_type=0, post_state=0, post_top_status=0,
        read_count=1, reply_count=2, like_count=3, forward_count=4,
        source_post_id=None, collected_at=NOW,
        source_times_raw={"post_publish_time": "2026-08-01 10:00:00"},
        source_metadata=list_title_metadata(
            {"final_urls": {"list": "https://guba.eastmoney.com/list,601012,f.html"}},
            title,
        ),
        raw_ref={"list": "seed-evidence"}, observation_version=1,
        final_url="https://guba.eastmoney.com/list,601012,f.html",
    )
    observation_id, version, created = store.record_observation(
        "seed-list", item, scope_key="stock:601012",
        evidence_links=[("seed-evidence", "list")],
        collector_version="test", parser_version=SCHEMA_VERSION,
    )
    assert created and version == 1
    store.finish_run("seed-list", status="SUCCESS", finished_at=NOW)
    store.close()
    return observation_id


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


def test_enrichment_waits_for_manual_challenge_then_retries(tmp_path):
    store=SimplePostStore(tmp_path/"collector.db")
    _ = _post = store.upsert_post(source="eastmoney_guba",source_item_id="1",stock_code="601012",title="x"*40,content=None,author_id="u",author_name="n",published_at="2026-08-01T02:00:00.000000Z",url="https://guba.eastmoney.com/news,601012,1.html",read_count=0,reply_count=0,like_count=0,forward_count=0)
    store.close()
    waits=[]
    report=execute_detail_enrichment(db_path=tmp_path/"collector.db",stock_code="601012",transport=BlockThenPass(),sleep_fn=waits.append,challenge_wait_seconds=7,challenge_retries=1)
    assert report["success"] == 1
    assert waits and waits[0] <= 5


def test_canonical_40_title_appends_detail_version_with_raw_lineage(tmp_path):
    first_observation_id = _seed_canonical_list_observation(tmp_path)
    transport = FixtureTransport("detail_enrichment_40_success.html")

    report = execute_detail_enrichment(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path,
        stock_code="601012", transport=transport, sleep_fn=lambda _: None,
        clock=lambda: NOW,
    )

    assert report["storage_mode"] == "canonical_observations"
    assert report["requested"] == report["success"] == 1
    assert report["canonical_observations_versioned"] == 1
    assert report["content_filled"] == 0
    assert report["candidates_remaining"] == 0
    assert [call[0] for call in transport.calls] == [DETAIL_URL]

    store = SQLitePersistence(
        tmp_path / "collector.db", RawEvidenceStore(tmp_path, SOURCE)
    )
    try:
        rows = store.conn.execute(
            """SELECT observation_id,observation_version,title,content,
                      content_sha256,source_metadata_json,drift_from_observation_id
                 FROM source_item_observations
                WHERE source_item_id='1754555652'
                ORDER BY observation_version"""
        ).fetchall()
        assert len(rows) == 2
        old, new = rows
        assert old[0] == first_observation_id and old[1] == 1
        assert old[3] == TRUNCATED_TITLE
        assert json.loads(old[5])["content_source"] == "list_title"
        assert json.loads(old[5])["list_title_suspected_truncated"] is True
        assert new[1] == 2 and new[6] == first_observation_id
        assert new[2] == TRUNCATED_TITLE
        assert len(new[3]) > 40 and new[3] != TRUNCATED_TITLE
        assert new[4] == hashlib.sha256(new[3].encode()).hexdigest()
        metadata = json.loads(new[5])
        assert metadata["content_source"] == "detail_body"
        assert metadata["detail_enrichment_trigger"] == "list_title_length_eq_40"
        assert metadata["detail_enrichment_from_observation_version"] == 1
        lineage = store.conn.execute(
            """SELECT oe.evidence_role,re.evidence_kind,re.content_sha256,
                      re.filesystem_path
                 FROM observation_evidence AS oe
                 JOIN raw_evidence AS re USING(evidence_id)
                WHERE oe.observation_id=?""",
            (new[0],),
        ).fetchone()
        assert lineage[0] == "detail"
        assert lineage[1] == "detail:browser_dom_snapshot"
        assert lineage[2] == hashlib.sha256(transport.payload).hexdigest()
        assert (tmp_path / lineage[3]).read_bytes() == transport.payload
    finally:
        store.close()


def test_canonical_40_title_failure_keeps_list_observation_and_records_reason(tmp_path):
    _seed_canonical_list_observation(tmp_path)
    transport = FixtureTransport("detail_enrichment_failure.html")

    report = execute_detail_enrichment(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path,
        stock_code="601012", transport=transport, sleep_fn=lambda _: None,
        clock=lambda: NOW,
    )

    assert report["success"] == 0 and report["failed"] == 1
    assert report["failures"][0]["reason"] == "detail_schema_mismatch"
    assert report["candidates_remaining"] == 1
    store = SQLitePersistence(
        tmp_path / "collector.db", RawEvidenceStore(tmp_path, SOURCE)
    )
    try:
        row = store.conn.execute(
            """SELECT observation_version,content,source_metadata_json
                 FROM source_item_observations
                WHERE source_item_id='1754555652'"""
        ).fetchone()
        assert row[0] == 1 and row[1] == TRUNCATED_TITLE
        assert json.loads(row[2])["content_source"] == "list_title"
        failure = store.conn.execute(
            """SELECT failure_class,message,evidence_id
                 FROM collection_failures WHERE run_id=?""",
            (report["run_id"],),
        ).fetchone()
        assert failure[0] == "detail_schema_mismatch"
        assert "post_article" in failure[1]
        assert failure[2] is not None
        evidence = store.conn.execute(
            "SELECT filesystem_path FROM raw_evidence WHERE evidence_id=?",
            (failure[2],),
        ).fetchone()
        assert (tmp_path / evidence[0]).read_bytes() == transport.payload
    finally:
        store.close()


def test_canonical_short_title_does_not_request_detail_by_default(tmp_path):
    _seed_canonical_list_observation(tmp_path, title="短标题")
    transport = FixtureTransport("detail_enrichment_40_success.html")

    report = execute_detail_enrichment(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path,
        stock_code="601012", transport=transport, sleep_fn=lambda _: None,
        clock=lambda: NOW,
    )

    assert report["requested"] == report["success"] == report["failed"] == 0
    assert transport.calls == []
    store = SQLitePersistence(
        tmp_path / "collector.db", RawEvidenceStore(tmp_path, SOURCE)
    )
    try:
        assert store.conn.execute(
            "SELECT count(*) FROM source_item_observations"
        ).fetchone()[0] == 1
        assert store.conn.execute(
            "SELECT status FROM collection_runs WHERE run_id=?", (report["run_id"],)
        ).fetchone()[0] == "NO_NEW_DATA"
    finally:
        store.close()


def test_canonical_short_title_can_be_explicitly_enriched(tmp_path):
    _seed_canonical_list_observation(tmp_path, title="短标题")
    transport = FixtureTransport("detail_enrichment_40_success.html")

    report = execute_detail_enrichment(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path,
        stock_code="601012", transport=transport, sleep_fn=lambda _: None,
        clock=lambda: NOW, include_short_titles=True,
    )

    assert report["requested"] == report["success"] == 1
    assert len(transport.calls) == 1
    store = SQLitePersistence(
        tmp_path / "collector.db", RawEvidenceStore(tmp_path, SOURCE)
    )
    try:
        metadata = json.loads(store.conn.execute(
            """SELECT source_metadata_json FROM source_item_observations
                WHERE source_item_id='1754555652'
                ORDER BY observation_version DESC LIMIT 1"""
        ).fetchone()[0])
        assert metadata["content_source"] == "detail_body"
        assert metadata["detail_enrichment_trigger"] == "explicit_short_title"
    finally:
        store.close()


def test_canonical_404_is_marked_skipped_without_touching_frozen_schema(tmp_path):
    """A missing post is ledgered in a sidecar file, never in the collector DB."""
    _seed_canonical_list_observation(tmp_path)
    transport = FixtureTransport("detail_enrichment_404.html")

    report = execute_detail_enrichment(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path,
        stock_code="601012", transport=transport, sleep_fn=lambda _: None,
        clock=lambda: NOW,
    )

    assert report["success"] == 0 and report["failed"] == 1
    assert report["failures"][0]["reason"] == "detail_not_found"
    assert report["skipped_not_found_added"] == 1
    # The only candidate is now remembered as missing.
    assert report["candidates_remaining"] == 0

    # The frozen collector schema is untouched: reopening it must not raise.
    store = SQLitePersistence(
        tmp_path / "collector.db", RawEvidenceStore(tmp_path, SOURCE)
    )
    try:
        assert store.conn.execute(
            "SELECT count(*) FROM source_item_observations"
        ).fetchone()[0] == 1
    finally:
        store.close()

    # The skip lives in a sibling ledger, keyed by the collector db stem.
    ledger = tmp_path / "collector.detail_enrichment_skips.db"
    assert ledger.is_file()
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(ledger)
    try:
        rows = conn.execute(
            "SELECT source, source_item_id, reason FROM detail_enrichment_skips"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(SOURCE, "1754555652", "detail_not_found")]


def test_canonical_404_is_not_refetched_on_second_run(tmp_path):
    _seed_canonical_list_observation(tmp_path)
    first = FixtureTransport("detail_enrichment_404.html")
    execute_detail_enrichment(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path,
        stock_code="601012", transport=first, sleep_fn=lambda _: None,
        clock=lambda: NOW,
    )
    assert len(first.calls) == 1

    second = FixtureTransport("detail_enrichment_404.html")
    report = execute_detail_enrichment(
        db_path=tmp_path / "collector.db", raw_data_dir=tmp_path,
        stock_code="601012", transport=second, sleep_fn=lambda _: None,
        clock=lambda: NOW,
    )
    # The known-missing post is skipped: no request is issued at all.
    assert second.calls == []
    assert report["requested"] == 0
    assert report["success"] == report["failed"] == 0


def test_legacy_404_is_marked_skipped_and_not_refetched(tmp_path):
    store = SimplePostStore(tmp_path / "collector.db")
    store.upsert_post(
        source="eastmoney_guba", source_item_id="1", stock_code="601012",
        title="x" * 40, content=None, author_id="u", author_name="n",
        published_at="2026-08-01T02:00:00.000000Z",
        url="https://guba.eastmoney.com/news,601012,1.html",
        read_count=0, reply_count=0, like_count=0, forward_count=0,
    )
    store.close()

    first = FixtureTransport("detail_enrichment_404.html")
    report = execute_detail_enrichment(
        db_path=tmp_path / "collector.db", stock_code="601012",
        transport=first, sleep_fn=lambda _: None, clock=lambda: NOW,
    )
    assert report["skipped_not_found_added"] == 1
    assert report["candidates_remaining"] == 0
    assert len(first.calls) == 1

    second = FixtureTransport("detail_enrichment_404.html")
    report2 = execute_detail_enrichment(
        db_path=tmp_path / "collector.db", stock_code="601012",
        transport=second, sleep_fn=lambda _: None, clock=lambda: NOW,
    )
    assert second.calls == []
    assert report2["requested"] == 0

    # The legacy posts table is still readable after the sidecar write.
    reopened = SimplePostStore(tmp_path / "collector.db")
    try:
        assert reopened.rows("eastmoney_guba", "601012")[0]["content"] is None
    finally:
        reopened.close()
