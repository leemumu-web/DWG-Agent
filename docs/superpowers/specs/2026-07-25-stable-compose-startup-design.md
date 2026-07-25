# Stable Compose Startup Design

## Goal

Make `bash scripts/docker.sh up-workers` a reliable production startup command. A zero exit code must mean that the complete Compose stack was rebuilt from the current checkout, every configured service reached a stable running state, and the public gateway and backend readiness probes passed.

## Scope

The change is limited to the existing Compose startup path in `scripts/lib/compose.sh`, its script tests, and operator documentation. It does not add another startup command, change application data, modify service definitions, or weaken any health check.

`bash scripts/docker.sh up` keeps its existing core-stack behavior. `up-workers` remains the single command for starting the complete production stack.

## Startup Flow

1. Validate `.env.docker`, Compose configuration, required source directories, and Docker availability through the existing `compose_check`.
2. Run the existing `docker compose --profile workers up -d --build --remove-orphans`.
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

## Testing

Script contract tests must prove that:

- `up-workers` delegates to the stable startup function;
- expected services are derived from `config --services`;
- the loop checks running and health state;
- restart and terminal states fail closed;
- timeout diagnostics include scoped status and logs;
- smoke probes execute only after the health gate succeeds.

The live release gate is:

1. run the focused infrastructure tests;
2. execute `bash scripts/docker.sh up-workers`;
3. verify every configured service is running and healthy;
4. execute `bash scripts/docker.sh smoke`;
5. confirm local and remote `main` point to the release commit.

## Non-Goals

- No automatic deletion or reset of MySQL or MinIO.
- No automatic repair loop for crashing services.
- No fixed list of 16 service names.
- No changes to local non-Compose startup scripts.
- No background success while services are still starting.
