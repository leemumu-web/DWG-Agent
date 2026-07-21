# Storage reconciliation

`scanning.py` compares the files-domain registry with configured MinIO buckets and persists only
findings; it never repairs data. `remediation.py` requires a short-lived signed preview, actor
binding, idempotency key, unchanged target digest, action/finding compatibility and bounded target
count/bytes. Permanent untracked-object deletion additionally requires `PURGE` and uses the file
transfer ledger for partial-failure visibility.

The `storage_scan_runs` and `storage_scan_findings` tables remain owned by `modules/files` because
they are facts about registered files. This module owns the operational use cases, not the storage
adapter or those ORM definitions.
A scan must never mutate objects; remediation cannot bypass its signed preview,
live digest recheck or explicit purge guard.
