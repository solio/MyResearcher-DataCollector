# Xueqiu DOM integration implementation report

## Current production runtime

The default Xueqiu acquisition mode is now `dedicated-chrome-cdp`.

```text
ordinary official Chrome executable
  + persistent .runtime/browser-profiles/xueqiu-dedicated
  + fixed 127.0.0.1:9227 CDP port
  + Playwright connect_over_cdp only
  + Target.createTarget(background=true)
  + stable facade across CDP reconnects
```

The implementation is based on the bounded live evidence in
`experts/xueqiu-live-access/2026-08-18-independent-chrome-cdp.md`. That run
loaded 9 page-1 posts, 10 page-2 posts with zero overlap, and detail
`405329188`; the owned Chrome PID did not become frontmost and the user's
Chrome baseline/final tab identity matched. Entry and detail each had one
self-recovered redacted `md5__1038` navigation, so the evidence is bounded and
does not prove long unattended availability.

## Components

- `sources/xueqiu/dedicated_chrome.py`: owns binary/profile/port/PID, profile
  lock, local proxy exclusion for the CDP driver, background target creation,
  CDP reconnect, public DOM facade, detail target close, redacted navigation
  diagnostics, action-boundary focus telemetry, and cleanup verification.
- `sources/xueqiu/dom_scripts.py`: one shared selector/challenge/DOM script
  source for both dedicated CDP and legacy Apple Events runtimes.
- `sources/xueqiu/dom_transport.py`: defaults to `dedicated-chrome-cdp`; target
  page progression now also requires a non-empty ID sequence, preventing a
  transient empty DOM from being accepted as page progress.
- `sources/xueqiu/browser_transport.py`: the approved JSON-response observer
  accepts a runtime safety callback and refuses a response when the owned page
  is in visible or repeated verification state.
- `cli/main.py`: both `xueqiu --confirm-live` and
  `backfill --source xueqiu --confirm-live` construct and close the dedicated
  runtime. Plan-only reports the fixed profile/port without constructing it.
- `sources/xueqiu/existing_chrome.py`: retained only as explicit legacy mode;
  shared DOM scripts moved out, but Apple Events behavior is otherwise kept for
  backward compatibility.

The DOM parser, range/coverage rules, page durability, post upsert semantics,
approved JSON field mapping, Eastmoney source, and batch source semantics were
not redesigned.

## Safety and lifecycle invariants

1. Never add `playwright.launch`, `launch_persistent_context`, headless mode,
   stealth or fingerprint overrides to `dedicated_chrome.py`.
2. Never replace background `Target.createTarget` with `context.new_page()`;
   the latter was live-observed to pull Chrome to the foreground.
3. Every externally created target is discovered after reconnect. Raw Page
   objects before reconnect are invalid and must remain inside the facade.
4. Main page identity after a detail reconnect is resolved by the exact stock
   path; zero or multiple candidates is a hard failure.
5. Profile lock and fixed port are both required. Port cleanup failure is a
   run failure, not a warning-only success.
6. Only the exact owned `Popen` PID may be terminated. No broad `pkill` or
   bundle-wide quit command is permitted.
7. Query values, Cookie/storage values and unrelated user-tab URL/title text
   must never be persisted. Focus telemetry stores only opaque hashes.
8. A visible challenge or more than the bounded transient navigation budget is
   an access failure. The code must not solve, click or bypass CAPTCHA.

## Compatibility modes

- `--acquisition-mode existing-chrome`: legacy Apple Events mode; may interfere
  with the user's Chrome and requires Apple Events JavaScript permission.
- `--acquisition-mode managed-chromium`: legacy Playwright-launched mode; live
  evidence showed `md5__1038` loops and it is not the default.
- `dedicated-chrome-cdp`: the only default production mode.

## Live state

The architecture itself has prior bounded live acceptance through the expert
probe. This implementation pass has deterministic coverage and does not yet
claim that the newly wired live CLI completed a new persisted production run.
Run a bounded `SH601012` live CLI smoke before calling the integration fully
production-accepted.
