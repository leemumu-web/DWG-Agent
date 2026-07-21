# Daily archive

The archive flow is preview-first and non-destructive. `planning.py` freezes the selected
Asia/Shanghai business-day registry rows and signs their manifest. `execution.py` revalidates the
frozen digest, streams the current objects into a ZIP, writes a JSON manifest, stores both in
MinIO and registers both files plus transfer-ledger outcomes in MySQL. `presentation.py` owns the
stable API projection.

No source object is moved or deleted. There is no automatic schedule or off-site backup; the
maintenance task is queued only by the authenticated API operation.
