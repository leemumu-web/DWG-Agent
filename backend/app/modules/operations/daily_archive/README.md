# Daily archive

The archive flow is preview-first and non-destructive. `planning.py` freezes the selected
Asia/Shanghai business-day registry rows and signs their manifest. `execution.py` revalidates the
frozen digest, streams the current objects into a ZIP, writes a JSON manifest, stores both in
MinIO and registers both files plus transfer-ledger outcomes in MySQL. `presentation.py` owns the
stable API projection. `models.py` owns the DailyArchiveRun ledger, `schemas.py` owns preview/run
response contracts, `routes.py` implements preview/create/list/detail authorization and audit, and
`tasks.py` registers the explicitly requested maintenance-queue execution entry.

No source object is moved or deleted. There is no automatic schedule or off-site backup; the
maintenance task is queued only by the authenticated API operation.
The archive must not be presented as a backup until retention, off-site copies
and a tested restore procedure exist.
