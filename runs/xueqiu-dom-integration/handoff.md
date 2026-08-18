# Xueqiu dedicated Chrome/CDP — Luna Developer handoff

Status: `IMPLEMENTED_OFFLINE_VERIFIED_LIVE_CLI_REACCEPTANCE_PENDING`

Next role: Luna Developer maintenance, then independent live Tester/Reviewer

## What changed

The old default `existing-chrome` path controlled the user's normal Chrome via
application-wide Apple Events. The new default `dedicated-chrome-cdp` path:

- starts the official Chrome binary directly;
- owns `.runtime/browser-profiles/xueqiu-dedicated`;
- owns fixed loopback port 9227 and an advisory profile lock;
- attaches through `connect_over_cdp` only;
- creates main/detail targets with `background=true`;
- reconnects CDP so Playwright discovers those external targets;
- keeps reconnecting raw Page objects behind a stable facade;
- terminates only its exact owned PID and verifies the port is closed.

Both production entry points use it:

```text
myresearcher-collector xueqiu 601012 ... --confirm-live
myresearcher-collector backfill --source xueqiu --stock 601012 ... --confirm-live
```

Legacy modes remain explicit and must not become default again.

## Luna maintenance map

Start here, in order:

1. `src/myresearcher_collector/sources/xueqiu/dedicated_chrome.py`
2. `src/myresearcher_collector/sources/xueqiu/dom_scripts.py`
3. `src/myresearcher_collector/sources/xueqiu/dom_transport.py`
4. `src/myresearcher_collector/sources/xueqiu/browser_transport.py`
5. `src/myresearcher_collector/cli/main.py`
6. `tests/unit/test_xueqiu_dedicated_chrome.py`
7. `specs/xueqiu.md`

The key abstraction is `XueqiuDedicatedChromePage`: callers see one stable
page-shaped object, even though its private Playwright Page changes after every
background detail target reconnect. Do not leak `_main_page`, `_browser` or
`_context` into parser, persistence or backfill code.

## Do-not-regress checklist

- [ ] no `playwright.launch` or `launch_persistent_context`;
- [ ] no `context.new_page()` for main/detail;
- [ ] `Target.createTarget` always includes `background: true`;
- [ ] fixed address is `127.0.0.1`, never `0.0.0.0`;
- [ ] profile lock acquired before Chrome launch;
- [ ] port free check happens before launch;
- [ ] only owned Popen PID is terminated;
- [ ] cleanup verifies fixed port closed;
- [ ] page 2 requires target page number, non-empty IDs and changed signature;
- [ ] detail ID is validated by existing parser/backfill logic;
- [ ] challenge query values are redacted, not logged;
- [ ] Cookie/storage/profile contents remain unread;
- [ ] AppleScript remains observation-only and stores unrelated URL/title only
  as hashes;
- [ ] plan-only never constructs runtime or writes profile/data state.

## Failure classification

Treat these as source/runtime failures, never `NO_NEW_DATA`:

- profile lock or fixed port collision;
- Chrome exits before CDP readiness;
- context count is not exactly one;
- background target is missing after reconnect;
- main page identity is zero or ambiguous after detail reconnect;
- visible verification/CAPTCHA;
- transient challenge navigation count exceeds the bounded budget;
- empty/duplicate/non-progressing page IDs;
- detail `SNOWMAN_STATUS` missing/invalid or detail ID mismatch;
- owned process cleanup leaves the CDP port open.

Do not add CAPTCHA solving, proxy/account rotation, signature generation,
Cookie export, UA/webdriver mutation or other fingerprint changes.

## Verification order

1. `python -m compileall -q src`
2. run Xueqiu dedicated/runtime/DOM/browser-transport/CLI unit tests;
3. run Xueqiu integration tests;
4. run the full repository suite;
5. plan-only smoke and confirm it does not create profile/port/process;
6. one bounded live `SH601012` smoke;
7. only if it passes, bounded 3-day persisted backfill;
8. only then consider 7-day or more stocks.

Do not jump directly to 30 stocks × 100 days. The live expert evidence had one
self-recovered `md5__1038` navigation on entry and detail, so scale remains an
external-risk question rather than a solved code property.
