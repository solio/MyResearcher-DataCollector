# RET-011 Migration Evidence

Stage A used the actual pre-retention production schema source from historical commit
`580150a24944c01992dadbac3d49fdb372871b96` (`SCHEMA_VERSION = 1`), executed its original
`MIGRATION_SQL`, and inserted one real run, attempt, raw evidence object, observation,
observation-evidence link, scope, and checkpoint plus its raw body file.

Stage B opened that unchanged database with Developer commit
`9f3cd2136b68bc11279b9b7fe1f6ab89a59207ea`.

Verified:

- `PRAGMA user_version` changed from 1 to 2;
- all six historical data groups remained present: run, attempt, raw evidence,
  observation, observation-evidence, checkpoint;
- checkpoint watermark remained unchanged;
- existing raw body path and bytes remained present;
- migrated `raw_body_state` initialized the existing object as `PRESENT`.

No database deletion, data-directory recreation, synthetic downgrade, or production edit was used.
