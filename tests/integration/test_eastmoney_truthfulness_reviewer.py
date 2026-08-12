"""Independent reviewer checks for Eastmoney truthfulness and provenance."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from myresearcher_collector.integration import execute_and_persist_collection
from myresearcher_collector.models import CollectionStatus
from myresearcher_collector.sources.eastmoney_guba import (
    CollectorConfig,
    EastmoneyGubaCollector,
    HttpResponse,
)
from myresearcher_collector.storage import RawEvidenceStore, SQLitePersistence


FIXTURES = Path(__file__).parents[1] / "fixtures" / "eastmoney_guba"
UTC = timezone.utc
NOW = datetime(2026, 8, 12, tzinfo=UTC)


class FixtureTransport:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float) -> HttpResponse:
        del timeout
        self.calls.append(url)
        value = self.routes[url]
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, HttpResponse)
        return value


def response(name: str, *, status: int = 200) -> HttpResponse:
    return HttpResponse(status, (FIXTURES / name).read_bytes(), {"content-type": "text/html"},
                        "https://guba.eastmoney.com/" + name)


def url(page: int) -> str:
    return EastmoneyGubaCollector.list_url("600001", page)


def detail_url(item_id: str) -> str:
    return f"https://guba.eastmoney.com/news,600001,{item_id}.html"


def config(max_pages: int = 3) -> CollectorConfig:
    return CollectorConfig(max_pages=max_pages, min_interval_seconds=2.5, base_backoff_seconds=0)


def run(tmp_path: Path, transport: FixtureTransport, run_id: str, *, max_pages: int = 3):
    return execute_and_persist_collection(
        db_path=tmp_path / "collector.db",
        raw_data_dir=tmp_path / "data",
        stock_code="600001",
        transport=transport,
        run_id=run_id,
        collector_config=config(max_pages),
        clock=lambda: NOW,
        sleep_fn=lambda _: None,
        max_pages=max_pages,
        bootstrap_if_no_checkpoint=False,
    )


def test_reviewer_http_evidence_is_parser_faithful_and_traceable(tmp_path: Path) -> None:
    list_body = (FIXTURES / "list_page_1.html").read_bytes()
    detail_body = (FIXTURES / "detail_1001.html").read_bytes()
    transport = FixtureTransport({
        url(1): response("list_page_1.html"),
        detail_url("1001"): response("detail_1001.html"),
        url(2): response("empty_page.html"),
    })

    execution = run(tmp_path, transport, "truthful-http", max_pages=2)
    assert execution.result.status is CollectionStatus.SUCCESS
    assert execution.result.items

    conn = sqlite3.connect(tmp_path / "collector.db")
    try:
        evidence = conn.execute(
            """SELECT e.evidence_id, e.request_url, e.final_url, e.content_sha256,
                      e.byte_size, e.filesystem_path, oe.evidence_role
                 FROM raw_evidence AS e
                 JOIN observation_evidence AS oe ON oe.evidence_id=e.evidence_id
                WHERE e.run_id='truthful-http'
                ORDER BY oe.evidence_role"""
        ).fetchall()
        assert [row[6] for row in evidence] == ["detail", "list"]
        assert len(evidence) == 2
        for evidence_id, request_url, final_url, digest, byte_size, relative_path, role in evidence:
            path = tmp_path / "data" / relative_path
            body = path.read_bytes()
            expected = detail_body if role == "detail" else list_body
            assert body == expected
            assert hashlib.sha256(body).hexdigest() == digest
            assert byte_size == len(body)
            assert request_url.startswith("https://guba.eastmoney.com/")
            assert final_url.startswith("https://guba.eastmoney.com/")
            assert conn.execute(
                "SELECT count(*) FROM raw_evidence WHERE evidence_id=?", (evidence_id,)
            ).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM source_item_observations").fetchone()[0] == 1
    finally:
        conn.close()


def test_reviewer_invalid_document_is_spec_mismatch_not_access_block(tmp_path: Path) -> None:
    transport = FixtureTransport({url(1): response("malformed_page.html")})
    execution = run(tmp_path, transport, "invalid-document", max_pages=1)
    assert execution.result.status is CollectionStatus.SPEC_MISMATCH
    assert execution.result.stop_reason == "schema_mismatch"
    assert execution.result.failures == ["page 1: schema_mismatch"]


def test_reviewer_challenge_page_is_access_block_and_not_normal_success(tmp_path: Path) -> None:
    transport = FixtureTransport({url(1): [response("verification_page.html"), response("verification_page.html")]})
    execution = run(tmp_path, transport, "challenge-page", max_pages=1)
    assert execution.result.status is CollectionStatus.COLLECTION_FAILED
    assert execution.result.stop_reason == "page_failure"
    assert execution.result.failures == ["page 1: access_block"]
    assert execution.result.counters.requests_success == 0


def test_reviewer_unresolved_page_gap_never_reaches_later_page_or_checkpoint(tmp_path: Path) -> None:
    transport = FixtureTransport({
        url(1): response("list_page_1.html"),
        detail_url("1001"): response("detail_1001.html"),
        url(2): response("empty_page.html", status=503),
        url(3): response("list_page_2.html"),
    })
    execution = run(tmp_path, transport, "page-gap", max_pages=3)
    assert execution.result.status is CollectionStatus.PARTIAL_COLLECTION
    assert execution.result.safe_frontier is None
    assert url(3) not in transport.calls
    conn = sqlite3.connect(tmp_path / "collector.db")
    try:
        assert conn.execute(
            "SELECT count(*) FROM collector_checkpoints WHERE source='eastmoney_guba'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT status, watermark_after_utc FROM collection_runs WHERE run_id='page-gap'"
        ).fetchone() == ("PARTIAL_COLLECTION", None)
    finally:
        conn.close()
