# Data catalog and infrastructure projections

This module owns the authenticated MySQL/MinIO data console over the files registry, transfer
ledger, configured object storage and bounded infrastructure probes. `queries.py` owns database projections,
`presentation.py` owns response dictionaries, `routes.py` owns `/data-admin` catalog operations,
`infrastructure.py` collects bounded database/storage/worker topology facts, and
`system_routes.py` retains the existing authenticated `/system` contract.

`mysql_gateway.py` signs and validates the short-lived CloudBeaver identity cookie;
`mysql_routes.py` exposes that bridge. `object_mutations.py` owns admin-only registered-object
move and soft-delete operations while reusing `app/modules/files/interface.py`. Consistency
remediation remains in its own guarded operations module. Non-admin users can inspect but cannot
invoke any mutation.
