"""The fleet body-patch bridge must refuse to guess which post a body belongs to.

The SQL that arrives from the control plane addresses rows by
``(instance_id, seq)`` and only names posts in a trailing ``SELECT ... IN``.
Applying it to the training library therefore rests on two assumptions that are
easy to get wrong and expensive to get wrong quietly:

* ``body_sha256`` is the plain sha256 of the UTF-8 body, not the
  JSON-canonical hash ``federation.py`` writes into ``versions``;
* the ``SELECT`` list is in the same order as the ``UPDATE`` statements.

Both are pinned here, including the negative controls: a tampered body and a
swapped id list must fail loudly rather than write the wrong text under the
right post.
"""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from scripts.ops.apply_fleet_body_patch import main, parse_patch, verify_bridge

BODY_A = "就长川谁买谁死，看看前两天的走势，全是骗人的诱多，爱谁买谁买，反正我不买，就是捡乐"
BODY_B = "$长川科技(SZ300604)$  国家大基金三期成立 注册资本3440亿\n近日，国家集成电路产业投资基金三期股份有限公司成立。"
TITLES = {"1430458891": BODY_A[:40], "1430434434": BODY_B[16:16 + 40]}


def _sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _sql(body_a: str, body_b: str, ids: tuple[str, str], *, sha_a: str | None = None) -> str:
    """Minimal reproduction of the control-plane payload (two rows).

    ``sha_a`` exists so a test can carry a body that does not match its hash --
    recomputing the hash from the tampered body would make the guard untestable.
    """
    hashes = (sha_a or _sha(body_a), _sha(body_b))
    statements = [
        "UPDATE versions SET payload = json_set(payload, '$.post.content', "
        f"'{body}', '$.post.body_complete', 1), body_sha256 = '{sha}' "
        "WHERE instance_id = 'i-1' AND seq = 2;" for body, sha in zip((body_a, body_b), hashes)
    ]
    listing = ", ".join(f"'{pid}'" for pid in ids)
    return ("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n"
            f"SELECT post_id FROM versions WHERE post_id IN ({listing});\n")


def _posts_db(path, rows):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE posts (source TEXT NOT NULL, source_item_id TEXT NOT NULL,
        stock_code TEXT NOT NULL, title TEXT, content TEXT, author_id TEXT, author_name TEXT,
        published_at TEXT NOT NULL, url TEXT NOT NULL, read_count INTEGER, reply_count INTEGER,
        like_count INTEGER, forward_count INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(source, source_item_id))""")
    for post_id, title, content in rows:
        db.execute("INSERT INTO posts VALUES('eastmoney_guba',?,?,?,?,NULL,NULL,"
                   "'2024-05-27T15:05:33+08:00',?,0,0,NULL,0,'2026-11-13T01:01:45.053937Z',"
                   "'2026-11-13T01:01:45.053937Z')",
                   (post_id, "300604", title, content, f"https://example.invalid/{post_id}"))
    db.commit()
    db.close()


def test_parse_patch_reads_bodies_and_hashes_them_as_plain_sha256(tmp_path):
    sql = tmp_path / "patch.sql"
    sql.write_text(_sql(BODY_A, BODY_B, ("1430458891", "1430434434")), encoding="utf-8")
    items = parse_patch(sql)
    assert [item["post_id"] for item in items] == ["1430458891", "1430434434"]
    assert [item["body"] for item in items] == [BODY_A, BODY_B]
    # negative control: the JSON-canonical hash must NOT be what the SQL carries
    canonical = hashlib.sha256(
        __import__("json").dumps(BODY_A, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode()).hexdigest()
    assert canonical != _sha(BODY_A)
    assert items[0]["sha256"] == _sha(BODY_A)


def test_parse_patch_refuses_a_tampered_body(tmp_path):
    sql = tmp_path / "patch.sql"
    tampered = BODY_A + "队"
    sql.write_text(_sql(tampered, BODY_B, ("1430458891", "1430434434"), sha_a=_sha(BODY_A)),
                   encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        parse_patch(sql)
    assert "sha256_mismatch" in str(excinfo.value)


def test_verify_bridge_accepts_the_declared_order():
    sql_body = [(BODY_A, "1430458891"), (BODY_B, "1430434434")]
    items = [{"post_id": pid, "body": body} for body, pid in sql_body]
    meta = {pid: {"title": TITLES[pid], "stock_code": "300604"} for pid in TITLES}
    verify_bridge(items, meta)  # prints BRIDGE_OK; no exception is the assertion


def test_verify_bridge_rejects_a_swapped_id_list():
    """The exact mistake an ordered SELECT invites -- and it must not pass."""
    items = [{"post_id": "1430434434", "body": BODY_A},
             {"post_id": "1430458891", "body": BODY_B}]
    meta = {pid: {"title": TITLES[pid], "stock_code": "300604"} for pid in TITLES}
    with pytest.raises(SystemExit) as excinfo:
        verify_bridge(items, meta)
    assert "title_bridge_failed" in str(excinfo.value)


def test_main_writes_once_then_skips_and_never_overwrites(tmp_path, capsys):
    sql = tmp_path / "patch.sql"
    sql.write_text(_sql(BODY_A, BODY_B, ("1430458891", "1430434434")), encoding="utf-8")
    db = tmp_path / "collector.db"
    _posts_db(db, [("1430458891", TITLES["1430458891"], None),
                   ("1430434434", TITLES["1430434434"], None)])
    log = tmp_path / "audit.jsonl"
    common = ["--sql", str(sql), "--db", str(db), "--audit-log", str(log)]

    assert main(common) == 0
    assert "written=2" in capsys.readouterr().out
    stored = dict(sqlite3.connect(db).execute("SELECT source_item_id, content FROM posts").fetchall())
    assert stored == {"1430458891": BODY_A, "1430434434": BODY_B}
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2

    # idempotent: a second run must not touch an existing body
    assert main(common) == 0
    second = capsys.readouterr().out
    assert "written=0 skipped=2" in second
    assert "PATCH_SKIP" in second
