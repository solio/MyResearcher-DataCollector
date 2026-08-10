# Phase 1 Round 05 — Failure Evidence

## Additional incremental mutable-fact failure

### Reproduction

1. Acquire the synthetic `list_page_1.html`/`detail_1001.html` observation as
   an existing item.
2. Re-run with `existing_observations={"1001": existing_item}`.
3. Keep `post_publish_time` at `2026-08-10 10:00:00 +08:00`, earlier than a
   committed watermark of `2026-08-10 11:00:00 +08:00`.
4. Change mutable list/detail facts (`post_last_time`, click count and body).
5. Follow with a valid empty page.

Observed:

```text
old_watermark_mutable_change NO_NEW_DATA empty_page 0 0 0
```

The request sequence contained only list pages; no detail request occurred.

### Expected frozen behavior

`specs/eastmoney_guba.md` section 6 requires changed source facts for the same
logical ID to become a new immutable observation/version with drift evidence.
Section 13 separately identifies engagement and `post_last_time` as mutable
snapshots. The committed watermark controls boundary eligibility; it does not
permit silently discarding changed facts for a known ID.

### Classification

`IMPLEMENTATION_DEFECT`

The implementation checks `published_at <= watermark` and returns before
acquiring detail, so the identity/version rule is not applied to old-window
mutable updates. This is not a `SPEC_MISMATCH`: the frozen spec states both
requirements explicitly and they are compatible when the old page is recorded
as an observation rather than treated as no-data.

### Next role

Developer

