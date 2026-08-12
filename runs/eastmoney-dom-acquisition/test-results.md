# Eastmoney DOM Acquisition — Test Results

Offline developer checks:

```text
git diff --check                                      PASS
compileall src tests                                  PASS
pytest -q                                             PASS (262 passed, 1 xfailed)
```

Coverage includes acquisition provenance, nullable HTTP metadata, DOM parser
integration, exact RawEvidence byte/SHA fidelity, SQLite lineage, equivalent
rerun idempotency, checkpoint isolation, access-block detection, CLI selection,
and unchanged HTTP-response transport tests.
