# Data catalog and infrastructure projections

This is the read-only operations view over the files registry, transfer ledger, configured object
storage and bounded infrastructure probes. `queries.py` owns database projections,
`presentation.py` owns response dictionaries, `routes.py` owns `/data-admin` catalog operations,
`infrastructure.py` collects bounded database/storage/worker topology facts, and
`system_routes.py` retains the existing authenticated `/system` contract.

The module does not upload, delete or repair files. Mutating file behavior remains behind
`app/modules/files/interface.py`; consistency remediation has its own guarded operations module.
