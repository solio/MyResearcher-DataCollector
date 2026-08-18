# Xueqiu dedicated Chrome/CDP deterministic test results

Final verification date: 2026-08-18

Commands were run from the repository root.

```text
git diff --check
PASS

PYTHONPYCACHEPREFIX=/tmp/myresearcher-xueqiu-final-pyc \
  python -m compileall -q src tests
PASS

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest \
  tests/unit/test_xueqiu_dedicated_chrome.py \
  tests/unit/test_xueqiu_dom.py \
  tests/unit/test_xueqiu_browser_transport.py \
  tests/unit/test_backfill_cli.py \
  tests/integration/test_xueqiu_dom_backfill.py -q
44 passed

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
351 passed, 1 xfailed
```

The standalone `xueqiu --plan-only` command was also run with a unique
nonexistent profile path and a fixed test port. It returned
`network_execution=false`, `acquisition_method=dedicated-chrome-cdp`, and the
profile path remained absent after the command. Therefore plan-only did not
start Chrome, bind CDP, create the profile, or mutate Collector data.

The new deterministic coverage includes:

- direct official-Chrome process arguments and exact owned-PID cleanup;
- fixed loopback CDP attach through a fake exposing `connect_over_cdp` but no
  Playwright launch API;
- background main/detail target creation and reconnect;
- dedicated profile lock and fixed-port validation;
- visible verification and repeated challenge fail-closed behavior;
- main-page preservation after temporary detail collection;
- non-empty and changed-ID pagination progress;
- response-observer safety callback enforcement;
- both production CLI entry points owning and reporting the new runtime;
- plan-only and incompatible acquisition-argument safety.

Earlier independent live evidence for the same runtime pattern is recorded in
`experts/xueqiu-live-access/2026-08-18-independent-chrome-cdp.md` as
`PASS_WITH_TRANSIENT_MD5_REDIRECTS`.

Production CLI live re-acceptance after this integration: **NOT EXECUTED**.
This document therefore does not claim unattended operation or validate
30 stocks × 100 days.
