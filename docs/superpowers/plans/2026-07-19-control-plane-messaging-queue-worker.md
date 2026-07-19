# Control-plane messaging, queue and worker plan

1. Add control-plane models and migration; update model exports and database
   verification expectations.
2. Add a service which records safe best-effort worker lifecycle/task activity,
   builds queue diagnostics from the MySQL SQL broker, and creates operational
   messages.
3. Add admin/auditor APIs for overview, events, messages and the explicit future
   Windows Node Agent contract.
4. Wire Celery signals plus local/Compose worker launch metadata; reserve the
   `dispatch` and `maintenance` queues without routing unfinished business tasks.
5. Extend the existing infrastructure frontend console with a clear runtime and
   communication tab and no fictitious controls.
6. Update architecture, operations, database and generated API docs; run migration,
   backend/frontend/static/browser checks; commit coherent milestones on
   `feat/messaging-worker-framework`.
