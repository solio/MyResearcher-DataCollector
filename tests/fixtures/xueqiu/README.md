# Sanitized Xueqiu acceptance fixtures

These fixtures are synthetic, offline-only JSON responses shaped according to
the approved `specs/xueqiu.md` response contract. They contain no captured
cookies, tokens, authorization headers, challenge values, signatures, or
unnecessary personal data.

- source: `xueqiu`
- observation: synthetic acceptance data, 2026-08-11
- sanitization: all IDs, names, URLs, text, timestamps and error bodies are
  deterministic placeholders; no browser profile or request headers are stored
- scenarios: two-page bootstrap, repeated page, invalid item, missing list and
  access/challenge failure
