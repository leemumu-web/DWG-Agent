# Storage, worker and UX hardening implementation plan

**Goal:** expose existing MySQL/object-storage truth, make one safe maintenance
worker operation executable, and harden every frontend route against common failure
states.

1. Write backend authorization and worker-task tests for queued stale-job recovery;
   verify they fail before the endpoint/task exists.
2. Implement the bounded maintenance task, queue endpoint and control-plane event
   persistence; regenerate API documentation.
3. Add frontend API client calls and extend the runtime/storage console with actual
   backend state and one explicit manual recovery control.
4. Add an app error boundary, connectivity banner and failure-aware React Query retry
   policy; test the visible fallback and build all routes.
5. Update operations/architecture/API docs; run backend/frontend/docs/Compose/live
   checks and commit each coherent slice on `main` before pushing `origin/main`.
