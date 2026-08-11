"""Read-only reporting over the persisted Collector runtime state."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _read_only_connection(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"collector database does not exist: {db_path}")
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _counter(counters: dict[str, Any], name: str) -> int:
    value = counters.get(name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def summarize_run(
    *,
    db_path: str | Path,
    raw_data_dir: str | Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Return required smoke observability using only persisted authority."""
    db_path = Path(db_path).expanduser().resolve()
    raw_data_dir = Path(raw_data_dir).expanduser().resolve()
    connection = _read_only_connection(db_path)
    try:
        if run_id is None:
            run = connection.execute(
                """SELECT * FROM collection_runs
                   ORDER BY started_at_utc DESC, rowid DESC LIMIT 1"""
            ).fetchone()
        else:
            run = connection.execute(
                "SELECT * FROM collection_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if run is None:
            target = "latest run" if run_id is None else f"run {run_id}"
            raise LookupError(f"{target} does not exist in {db_path}")

        persisted_run_id = str(run["run_id"])
        source = str(run["source"])
        scope_key = str(run["scope_key"])
        stock_code = scope_key.removeprefix("stock:") if scope_key.startswith("stock:") else None
        counters_value = json.loads(run["counters_json"])
        counters = counters_value if isinstance(counters_value, dict) else {}

        evidence = connection.execute(
            """SELECT count(*) AS evidence_count,
                      count(DISTINCT filesystem_path) AS file_count
               FROM raw_evidence WHERE run_id = ?""",
            (persisted_run_id,),
        ).fetchone()
        attempts_count = connection.execute(
            "SELECT count(*) FROM collection_attempts WHERE run_id = ?",
            (persisted_run_id,),
        ).fetchone()[0]
        failures_count = connection.execute(
            "SELECT count(*) FROM collection_failures WHERE run_id = ?",
            (persisted_run_id,),
        ).fetchone()[0]
        published_range = connection.execute(
            """SELECT min(o.published_at_utc), max(o.published_at_utc)
               FROM source_item_observations AS o
               JOIN observation_evidence AS oe
                 ON oe.observation_id = o.observation_id
               JOIN raw_evidence AS e ON e.evidence_id = oe.evidence_id
               WHERE e.run_id = ?""",
            (persisted_run_id,),
        ).fetchone()

        checkpoint_before = run["watermark_before_utc"]
        persisted_checkpoint_after = run["watermark_after_utc"]
        checkpoint_after = (
            persisted_checkpoint_after
            if persisted_checkpoint_after is not None
            else checkpoint_before
        )
        return {
            "run_id": persisted_run_id,
            "source": source,
            "stock_code": stock_code,
            "scope_key": scope_key,
            "status": run["status"],
            "requests_total": _counter(counters, "requests_total"),
            "requests_success": _counter(counters, "requests_success"),
            "requests_failed": _counter(counters, "requests_failed"),
            "pages_requested": _counter(counters, "pages_requested"),
            "pages_success": _counter(counters, "pages_success"),
            "pages_failed": _counter(counters, "pages_failed"),
            "records_received": _counter(counters, "records_received"),
            # The approved runtime calls accepted records ``records_parsed``.
            "records_accepted": _counter(counters, "records_parsed"),
            "records_failed": _counter(counters, "records_failed"),
            "raw_evidence_location": str(raw_data_dir / "raw" / source),
            "raw_evidence_count": int(evidence["evidence_count"]),
            "raw_evidence_file_count": int(evidence["file_count"]),
            "sqlite_location": str(db_path),
            "checkpoint_before": checkpoint_before,
            "checkpoint_after": checkpoint_after,
            "checkpoint_updated": persisted_checkpoint_after is not None,
            "safe_frontier": run["safe_frontier_utc"],
            "first_published_at": published_range[0],
            "last_published_at": published_range[1],
            "attempts_persisted": int(attempts_count),
            "failures_persisted": int(failures_count),
        }
    finally:
        connection.close()
