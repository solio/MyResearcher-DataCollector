# Scripts

This directory is reserved for future deterministic project utilities such as local contract checks or fixture validation.

Phase 0 contains no runtime, source-probing, migration or network script. Scripts must obey the same contracts as package code and must not become an undocumented production entry point.

## `ops/`

`ops/` holds the documented operational runbooks for Eastmoney detail
enrichment (`enrich_all_stocks.sh`, `mop_up.sh`, `check_revisit.py`). They drive
the CLI and touch the network/browser, so they are deliberately kept out of this
directory root rather than mixed with deterministic utilities.

They write only to the gitignored `runtime/` and `data/`, and each one is
documented in [`ops/README.md`](ops/README.md) — the "no undocumented production
entry point" rule above is satisfied by that file, not by hiding the scripts.
