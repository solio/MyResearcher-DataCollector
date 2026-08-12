# Eastmoney Existing-Chrome DOM Acquisition — Developer Scope

Architecture correction only: treat HTTP responses and existing-user Chrome
DOM snapshots as distinct acquisition methods sharing the production parser,
RawEvidence, persistence and Backfill pipeline. HTTP metadata remains strict for
HTTP acquisition and nullable for DOM acquisition. Full Backfill is not
authorized; one bounded stock/window smoke is authorized after offline tests.
