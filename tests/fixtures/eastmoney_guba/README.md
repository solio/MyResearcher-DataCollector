# Eastmoney Guba fixtures

These are synthetic, cropped and deterministic fixtures derived from the approved source structure. IDs, author values and text are synthetic; no live response, cookie, authorization header or unnecessary personal data is stored.

Source/spec: `eastmoney_guba`, observed structure documented in `runs/phase-01-round-01/research-evidence.md` and `specs/eastmoney_guba.md`.

Scenarios:

- `list_page_1.html`: one accepted `post_type=0` row plus one preserved/countable alternate row;
- `list_page_2.html`: one overlapping ID plus one new accepted row;
- `empty_page.html`: valid source-success empty page;
- `malformed_page.html`: missing embedded source object;
- `detail_1001.html`, `detail_1002.html`: matching standard detail pages.
