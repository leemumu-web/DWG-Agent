# Security

> Chinese mirror: [zh/security.md](zh/security.md)

## Trust Boundaries

Nginx and the frontend are not authorization boundaries. Every business endpoint authenticates in FastAPI and enforces global roles plus resource/project access. MySQL and object storage are private network services in Compose.

## Authentication

- Passwords use Argon2id.
- Access JWTs default to 30 minutes; refresh cookies default to 14 days.
- Access and refresh token types are checked and cannot be exchanged.
- Logout writes token JTIs to MySQL `token_blacklist`.
- Password changes write `password_changed_at`; older tokens are rejected.
- Disabled/deleted users are checked on each authenticated request.
- The frontend keeps access state in `sessionStorage`, reducing cross-tab persistence.
- Refresh cookies are HttpOnly, SameSite, and Secure in production unless explicitly overridden for a private HTTP-only VPN.

There is no fail-open revocation path: token state is in authoritative MySQL, not an optional cache.

## SSE Authentication

Native EventSource cannot set a Bearer header. The API issues the short-lived HttpOnly `dwg_sse_token` cookie and the SSE dependency validates it. Tokens are never accepted in event-stream query strings. Normal job access checks run before streaming.

## Authorization

Global roles: `super_admin`, `admin`, `engineer`, `reviewer`, `operator`, `viewer`, `auditor`.

Project resources require membership and an allowed project role. Administrative global roles have explicit global project access; other roles do not. Super-admin targets cannot be disabled, deleted, reset, or role-managed by ordinary admins.

File reads require one of:

- administrative global access;
- uploader ownership;
- membership in an active project linked through a drawing version or analysis result.

File list and batch metadata use the same SQL access filter. Batch endpoints must not reveal metadata for an inaccessible file. Result details, result download URLs, and reviews delegate to the parent job boundary; for jobs without a project, only administrators and the creator have access. Agent runs, when enabled, require creator/admin/linked-project access for both details and steps.

## Job Integrity

Every retry creates a new `attempt`. Claim, progress, terminal state, cancellation, dispatch compensation, and stale recovery use conditional updates that include status and attempt. This prevents stale workers from overwriting a retry.

`job_steps.attempt` preserves history without mixing generations. Cancel-all locks the exact active IDs, changes only those rows, and reports broker purge results per queue.

## File Security

- Filename normalization removes traversal, control characters, separators, and unsafe leading characters.
- Extensions are allow-listed.
- DWG uploads require supported AC headers and a minimum size.
- Uploads are streamed with maximum size and SHA-256/MD5 calculation.
- ZIP extraction limits entry count and total uncompressed bytes and rejects traversal.
- Object keys are generated, not user-controlled paths.
- Database rollback compensates objects written before commit.

## Downloads

A signed URL is not sufficient by itself. The download endpoint also requires Bearer authentication and current file permission. The HMAC binds file ID and expiry. Frontend retries obtain a new signature instead of replaying an expired URL.

## Error Handling

Unexpected exceptions are logged server-side. Production responses use stable error codes and generic messages. Child-process stderr, traceback, DSN, secret, and host paths must not be stored in client-visible `jobs.error_message`.

Excel Final parser failures are mapped to a bounded public message; the full child traceback remains in worker logs.

## Database and Broker

- MySQL credentials are URL-encoded when building DSNs.
- Application pools are bounded and recycled.
- Celery uses `READ COMMITTED` and a queue-ordering index to reduce lock scope.
- SQL transport fanout control is disabled.
- Compose does not publish MySQL or MinIO host ports.
- The handbook grant is `SELECT` only.

## Audit

Login/logout, user lifecycle, role changes, project/member changes, file upload/download/delete, job lifecycle, review decisions, and sensitive operations write immutable audit rows. Audit access is restricted to `super_admin` and `auditor`.

## Production Checklist

- Replace every `CHANGE_ME_*`, JWT secret, admin password, MySQL password, and MinIO secret.
- Use TLS; keep secure refresh cookies enabled on public networks.
- Restrict Nginx origins and CORS to deployed frontend origins.
- Keep MySQL/MinIO on private networks and protect volume backups.
- Run migration tests and security boundary tests before rollout.
- Verify that `/health/ready` does not expose credentials or internal exceptions.
- Rotate credentials and invalidate sessions after a suspected compromise.
- Review audit logs and storage integrity hashes.

## Security Tests

Regression coverage includes token confusion, disabled users, super-admin protection, project isolation, unscoped result isolation, file ownership/membership, signed URL expiry/tampering, batch metadata access, constant-query file filtering, Agent run isolation, attempt races, storage compensation, and safe error messages.
