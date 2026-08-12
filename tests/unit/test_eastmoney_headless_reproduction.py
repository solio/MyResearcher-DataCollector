from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experts" / "eastmoney-live-access" / "reproduce_headless.py"
SPEC = importlib.util.spec_from_file_location("eastmoney_headless_reproduction", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _args(tmp_path: Path, *, pages: int = 1, with_detail: bool = True):
    return SimpleNamespace(
        chrome="/fake/chrome",
        stock="600001",
        pages=pages,
        timeout=30.0,
        request_interval=3.0,
        with_detail=with_detail,
        json_out=None,
        markdown_out=None,
    )


def test_fixture_run_reproduces_bounded_list_and_detail_contract(
    tmp_path: Path, monkeypatch
) -> None:
    list_html = (ROOT / "tests/fixtures/eastmoney_guba/list_page_1.html").read_text()
    detail_html = (ROOT / "tests/fixtures/eastmoney_guba/detail_1001.html").read_text()

    monkeypatch.setattr(MODULE, "_find_chrome", lambda _explicit: "/fake/chrome")
    monkeypatch.setattr(MODULE, "_chrome_version", lambda _executable: "Fixture Chrome")
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        MODULE,
        "_dump_dom",
        lambda _executable, _profile, url, _timeout: (
            detail_html if "/news," in url else list_html
        ),
    )

    result = MODULE.reproduce(_args(tmp_path))

    assert result["status"] == "PASS"
    assert result["visited_urls"] == [
        "https://guba.eastmoney.com/list,600001,f.html",
        "https://guba.eastmoney.com/news,600001,1001.html",
    ]
    assert result["pages"][0]["in_scope_count"] == 1
    assert result["pages"][0]["out_of_scope_count"] == 1
    assert result["pages"][0]["posts"][0]["source_item_id"] == "1001"
    assert result["detail_proof"]["list_detail_id_match"] is True
    assert result["detail_proof"]["content_length"] == len("synthetic body one")


def test_reproduction_preserves_access_block_status_and_visited_url(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(MODULE, "_find_chrome", lambda _explicit: "/fake/chrome")

    def blocked(_executable, _profile, url, _timeout):
        raise MODULE.ReproductionFailure("ACCESS_BLOCK", f"blocked {url}", 4)

    monkeypatch.setattr(MODULE, "_dump_dom", blocked)

    with pytest.raises(MODULE.ReproductionFailure) as caught:
        MODULE.reproduce(_args(tmp_path))

    assert caught.value.status == "ACCESS_BLOCK"
    assert caught.value.exit_code == 4
    assert caught.value.visited_urls == (
        "https://guba.eastmoney.com/list,600001,f.html",
    )


def test_reproduction_rejects_identical_page_signatures(
    tmp_path: Path, monkeypatch
) -> None:
    list_html = (ROOT / "tests/fixtures/eastmoney_guba/list_page_1.html").read_text()
    monkeypatch.setattr(MODULE, "_find_chrome", lambda _explicit: "/fake/chrome")
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        MODULE,
        "_dump_dom",
        lambda _executable, _profile, _url, _timeout: list_html,
    )

    with pytest.raises(MODULE.ReproductionFailure) as caught:
        MODULE.reproduce(_args(tmp_path, pages=2, with_detail=False))

    assert caught.value.status == "PAGINATION_NOT_PROGRESSING"
    assert caught.value.exit_code == 6


def test_cli_validators_enforce_bounded_inputs() -> None:
    parser = MODULE.build_parser()
    args = parser.parse_args(["--stock", "600001", "--pages", "2", "--no-with-detail"])
    assert args.stock == "600001"
    assert args.pages == 2
    assert args.with_detail is False

    with pytest.raises(SystemExit):
        parser.parse_args(["--stock", "60001"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--request-interval", "2.4"])
