# Stable Full-Stack Startup Design

## Goal

Make both production startup entrypoints reliable. A zero exit code from
`bash scripts/docker.sh up-workers` or `bash scripts/start-all.sh` must mean
that the selected complete stack was built from the current checkout and its
database, workers, backend, frontend, and gateway passed their readiness checks.

## Scope

The change is limited to the existing Compose startup path in
`scripts/lib/compose.sh`, the host startup path in `scripts/start-all.sh`, their
script tests, and operator documentation. It does not add another startup
command, change application data, modify service definitions, or weaken any
health check.

`bash scripts/docker.sh up` and `up-workers` rebuild from the current checkout
and force-recreate their selected containers. `up-workers` remains the single
command for starting the complete production stack. `down` always includes the
workers profile so no conversion container is left behind.

`bash scripts/start-all.sh` remains the stable host-process entrypoint. It
stops existing project Compose and host application processes, synchronizes
locked backend dependencies, restarts every worker and backend process, rebuilds
the frontend, restarts Nginx, then runs the existing `scripts/status.sh` as a
final fail-closed readiness gate before printing the success summary. MySQL
data and volumes remain intact.

## Startup Flow

1. Validate `.env.docker`, Compose configuration, required source directories, and Docker availability through the existing `compose_check`.
2. Run `docker compose --profile workers up -d --build --force-recreate --remove-orphans`.
3. Derive the expected service names from `docker compose --profile workers config --services`; do not hardcode a service count.
4. Poll `docker compose --profile workers ps --format json` until every expected service:
   - is present;
   - has state `running`;
   - has health `healthy`, or has no configured health check.
5. Treat `exited`, `dead`, `removing`, or `restarting` as an immediate startup failure. Treat `created` and health `starting` as transitional states until the timeout.
6. Use a 180-second deadline with a two-second polling interval. Do not use one fixed sleep.
7. After all services are healthy, run the existing public Nginx and backend readiness smoke probes.
8. Print a concise success line containing the verified service count.

## Failure Behavior

On an immediate failure or timeout, the command must:

- print each service that is absent, non-running, unhealthy, or still starting;
- print `docker compose ps` for the complete profile;
- print the last 80 log lines for only the affected services that exist;
- return a non-zero exit status.

Diagnostics must not print `.env.docker`, credentials, signed URLs, authorization headers, or object keys.

For host startup, a failing `scripts/status.sh` check prevents the success
banner and returns non-zero. The status output remains the primary diagnostic:
it identifies unavailable MySQL, missing workers, stale backend/frontend code,
failed backend readiness, and failed Nginx API/SPA probes without exposing
credentials.

## Interfaces

`compose_wait_for_healthy_services` owns the polling and diagnostic behavior. It accepts the timeout in seconds, defaults to 180, and uses the existing `COMPOSE_CMD` array so the configured project directory and environment file remain authoritative.

`compose_up_workers` owns the ordered startup transaction:

```text
compose_check
compose up --profile workers
compose_wait_for_healthy_services
compose_smoke
```

`compose_main` delegates `up-workers` to `compose_up_workers`. Existing commands and arguments remain compatible.

`scripts/start-all.sh` uses seven ordered steps:

```text
stop old runtime + sync -> MySQL -> local workers -> backend -> frontend build -> Nginx -> status.sh
```

The success summary is printed only after `status.sh` returns zero.

## Testing

Script contract tests must prove that:

- `up-workers` delegates to the stable startup function;
- expected services are derived from `config --services`;
- the loop checks running and health state;
- restart and terminal states fail closed;
- timeout diagnostics include scoped status and logs;
- smoke probes execute only after the health gate succeeds.

Host-start contract tests must prove that:

- existing Compose and local application instances are stopped before startup;
- backend locked dependencies are synchronized and the frontend always rebuilds;
- `start-all.sh` invokes `scripts/status.sh` after Nginx startup;
- a non-zero status result exits before the success banner;
- the documented command explains that startup success includes the final
  readiness gate.

The live release gate is:

1. run the focused infrastructure tests;
2. execute `bash scripts/docker.sh up-workers`;
3. verify every configured service is running and healthy;
4. execute `bash scripts/docker.sh smoke`;
5. confirm local and remote `main` point to the release commit.

The host-path release gate is run separately from Compose because both stacks
cannot own ports 8010/8080 and the same worker queues simultaneously:

1. stop the Compose stack while preserving its volumes;
2. run `bash scripts/start-all.sh`;
3. require `bash scripts/status.sh` to pass;
4. stop the host stack;
5. restore the complete Compose stack with `bash scripts/docker.sh up-workers`.

## Non-Goals

- No automatic deletion or reset of MySQL or MinIO.
- No automatic repair loop for crashing services.
- No fixed list of 16 service names.
- No new host-process supervisor or duplicated status implementation.
- No background success while services are still starting.
