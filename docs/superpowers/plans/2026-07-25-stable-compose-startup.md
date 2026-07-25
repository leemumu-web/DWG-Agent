# Stable Full-Stack Startup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both `bash scripts/docker.sh up-workers` and `bash scripts/start-all.sh` return success only after their selected complete stack passes readiness checks.

**Architecture:** Keep the stable command facades and add three focused functions to `scripts/lib/compose.sh`: state polling, failure diagnostics, and the ordered Compose startup transaction. Expected services come from Compose itself. The host startup reuses `scripts/status.sh` as its final fail-closed gate instead of duplicating process and HTTP checks.

**Tech Stack:** Bash, Docker Compose v2, pytest.

---

## Task 1: Lock the startup state machine with failing tests

**Files:**
- Modify: `backend/tests/infrastructure/test_scripts.py`
- Test: `backend/tests/infrastructure/test_scripts.py`

- [ ] **Step 1: Add a fake Compose harness**

Add a helper that writes an executable script accepting `config --services`, `ps --all --format`, `ps`, and `logs`. It reads `FAKE_COMPOSE_SCENARIO`, emits either healthy, restarting, or starting service rows, and records commands in `FAKE_COMPOSE_CALLS`.

```python
def _write_fake_compose(tmp_path):
    fake = tmp_path / "fake-compose"
    fake.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_COMPOSE_CALLS"
if [[ "$*" == *"config --services"* ]]; then
    printf 'api\\nworker\\n'
elif [[ "$*" == *"ps --all --format"* ]]; then
    case "$FAKE_COMPOSE_SCENARIO" in
        healthy) printf 'api|running|healthy\\nworker|running|healthy\\n' ;;
        restarting) printf 'api|running|healthy\\nworker|restarting|unhealthy\\n' ;;
        starting) printf 'api|running|healthy\\nworker|running|starting\\n' ;;
    esac
elif [[ "$*" == *" logs "* ]]; then
    printf 'worker diagnostic\\n'
else
    printf 'compose status\\n'
fi
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake
```

- [ ] **Step 2: Add behavior tests**

Execute Bash with `source scripts/lib/compose.sh`, replace `COMPOSE_CMD` with the fake, and assert:

```python
def _run_compose_health_case(tmp_path, scenario: str, *, timeout: int):
    fake = _write_fake_compose(tmp_path)
    calls_path = tmp_path / "calls.log"
    env = {
        **os.environ,
        "FAKE_COMPOSE_SCENARIO": scenario,
        "FAKE_COMPOSE_CALLS": str(calls_path),
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f'source "{PROJECT_ROOT}/scripts/lib/compose.sh"; '
                f'COMPOSE_CMD=("{fake}"); '
                f"compose_wait_for_healthy_services {timeout}"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    calls = calls_path.read_text(encoding="utf-8")
    return result, calls


def test_compose_health_gate_accepts_only_complete_healthy_stack(tmp_path):
    result, calls = _run_compose_health_case(tmp_path, "healthy", timeout=1)
    assert result.returncode == 0
    assert "2 services healthy" in result.stdout
    assert "config --services" in calls
    assert "ps --all --format" in calls


def test_compose_health_gate_fails_fast_and_scopes_logs(tmp_path):
    result, calls = _run_compose_health_case(tmp_path, "restarting", timeout=10)
    assert result.returncode != 0
    assert "worker" in result.stderr
    assert "logs --tail=80 worker" in calls


def test_compose_health_gate_times_out_starting_services(tmp_path):
    result, calls = _run_compose_health_case(tmp_path, "starting", timeout=0)
    assert result.returncode != 0
    assert "startup timed out" in result.stderr
    assert "logs --tail=80 worker" in calls


def test_stable_compose_startup_orders_health_gate_before_smoke():
    content = _read("scripts/lib/compose.sh")
    body = content[
        content.index("compose_up_workers()")
        : content.index("compose_backup()")
    ]
    assert body.index("compose_wait_for_healthy_services") < body.index("compose_smoke")
    assert "up-workers) compose_up_workers" in content
```

Add a contract assertion that `up-workers` delegates to `compose_up_workers`, and that `compose_up_workers` calls health waiting before smoke.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
cd backend
uv run pytest tests/infrastructure/test_scripts.py -k "compose_health_gate or stable_compose_startup" -q
```

Expected: failures because `compose_wait_for_healthy_services` and `compose_up_workers` do not exist.

## Task 2: Implement the fail-closed full-stack startup

**Files:**
- Modify: `scripts/lib/compose.sh`
- Test: `backend/tests/infrastructure/test_scripts.py`

- [ ] **Step 1: Add smoke and diagnostic functions**

Extract the existing readiness probes into:

```bash
compose_smoke() {
    compose_require_env
    local port
    port=$(compose_public_port)
    curl -fsS "http://127.0.0.1:${port}/nginx-health" >/dev/null
    curl -fsS "http://127.0.0.1:${port}/health/ready" >/dev/null
    compose_info "public gateway and backend readiness checks passed"
}
```

Add:

```bash
compose_startup_diagnostics() {
    local -a affected_services=("$@")
    compose_warn "full-stack startup did not reach a healthy state"
    "${COMPOSE_CMD[@]}" --profile workers ps --all >&2 || true
    if [ "${#affected_services[@]}" -gt 0 ]; then
        "${COMPOSE_CMD[@]}" --profile workers logs --tail=80 \
            "${affected_services[@]}" >&2 || true
    fi
}
```

- [ ] **Step 2: Implement conditional health polling**

Implement `compose_wait_for_healthy_services` with:

```bash
compose_wait_for_healthy_services() {
    local timeout="${1:-180}" deadline=$((SECONDS + timeout))
    local expected_output rows_output service state health item
    local terminal healthy_count
    local -a expected rows affected_labels affected_services
    declare -A states=() health_states=() seen=()

    if ! expected_output="$("${COMPOSE_CMD[@]}" --profile workers config --services)"; then
        compose_warn "failed to read expected Compose services"
        return 1
    fi
    mapfile -t expected <<<"$expected_output"
    [ "${#expected[@]}" -gt 0 ] && [ -n "${expected[0]}" ] || {
        compose_warn "Compose returned no expected services"
        return 1
    }

    while true; do
        if ! rows_output="$("${COMPOSE_CMD[@]}" --profile workers ps --all \
            --format '{{.Service}}|{{.State}}|{{.Health}}')"; then
            compose_warn "failed to inspect Compose service state"
            return 1
        fi
        mapfile -t rows <<<"$rows_output"
        states=()
        health_states=()
        seen=()
        for item in "${rows[@]}"; do
            [ -n "$item" ] || continue
            IFS='|' read -r service state health <<<"$item"
            [ -n "$service" ] || continue
            states["$service"]="$state"
            health_states["$service"]="$health"
            seen["$service"]=1
        done

        terminal=false
        healthy_count=0
        affected_labels=()
        affected_services=()
        for service in "${expected[@]}"; do
            if [ -z "${seen[$service]+x}" ]; then
                affected_labels+=("${service}=missing")
                continue
            fi
            state="${states[$service]}"
            health="${health_states[$service]}"
            if [ "$state" = "running" ] && { [ -z "$health" ] || [ "$health" = "healthy" ]; }; then
                healthy_count=$((healthy_count + 1))
                continue
            fi
            affected_labels+=("${service}=state:${state:-unknown},health:${health:-none}")
            affected_services+=("$service")
            case "$state:$health" in
                restarting:*|exited:*|dead:*|removing:*|running:unhealthy)
                    terminal=true
                    ;;
                created:*|running:starting)
                    ;;
                *)
                    terminal=true
                    ;;
            esac
        done

        if [ "$healthy_count" -eq "${#expected[@]}" ]; then
            compose_info "${healthy_count} services healthy"
            return 0
        fi
        if $terminal; then
            printf 'ERROR: service not ready: %s\n' "${affected_labels[@]}" >&2
            compose_startup_diagnostics "${affected_services[@]}"
            return 1
        fi
        if [ "$SECONDS" -ge "$deadline" ]; then
            printf 'ERROR: startup timed out: %s\n' "${affected_labels[@]}" >&2
            compose_startup_diagnostics "${affected_services[@]}"
            return 1
        fi
        sleep 2
    done
}
```

This classifies `restarting`, `exited`, `dead`, `removing`, unknown states, and health `unhealthy` as terminal failures; missing, `created`, and health `starting` remain pending until the deadline.

- [ ] **Step 3: Wire the stable command**

Add:

```bash
compose_up_workers() {
    compose_check
    "${COMPOSE_CMD[@]}" --profile workers up -d --build --remove-orphans
    compose_wait_for_healthy_services 180
    compose_smoke
}
```

Change `compose_main` so `up-workers)` calls `compose_up_workers`, and `smoke)` calls `compose_smoke`.

- [ ] **Step 4: Run focused and infrastructure tests**

Run:

```bash
cd backend
uv run pytest tests/infrastructure/test_scripts.py -k "compose_health_gate or stable_compose_startup" -q
uv run pytest tests/infrastructure/test_scripts.py tests/infrastructure/test_compose.py -q
```

Expected: all selected tests pass.

## Task 3: Gate the host startup on the existing status contract

**Files:**
- Modify: `scripts/start-all.sh`
- Modify: `backend/tests/infrastructure/test_scripts.py`
- Test: `backend/tests/infrastructure/test_scripts.py`

- [ ] **Step 1: Add a failing host-start contract test**

Add:

```python
def test_start_all_runs_final_status_gate_before_success_banner():
    content = _read("scripts/start-all.sh")
    status_index = content.index('bash "$PROJECT_ROOT/scripts/status.sh"')
    summary_index = content.index("全栈启动完成")
    assert status_index < summary_index
    assert 'if ! bash "$PROJECT_ROOT/scripts/status.sh"; then' in content
    assert "exit 1" in content[status_index:summary_index]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd backend
uv run pytest tests/infrastructure/test_scripts.py -k start_all_runs_final_status_gate -q
```

Expected: failure because `start-all.sh` does not yet invoke the final status gate.

- [ ] **Step 3: Add the sixth verification step**

After Nginx startup and before the success summary, add:

```bash
step "6/6 全栈就绪验证"
if ! bash "$PROJECT_ROOT/scripts/status.sh"; then
    err "全栈启动后验证失败；请按上方诊断处理后重试"
    exit 1
fi
```

Change the existing section labels from `1/5` through `5/5` to `1/6` through `5/6`.

- [ ] **Step 4: Run the focused test and syntax check**

Run:

```bash
cd backend
uv run pytest tests/infrastructure/test_scripts.py -k start_all_runs_final_status_gate -q
cd ..
bash -n scripts/start-all.sh scripts/status.sh
```

Expected: test and Bash syntax checks pass.

## Task 4: Document and verify both production paths

**Files:**
- Modify: `scripts/README.md`
- Test: `backend/tests/infrastructure/test_scripts.py`

- [ ] **Step 1: Add the documentation contract test**

Add:

```python
def test_stable_startup_docs_cover_compose_and_host_health_gates():
    content = _read("scripts/README.md")
    assert "up-workers" in content
    assert "180 秒" in content
    assert "80 行日志" in content
    assert "scripts/status.sh" in content
    assert "全部受管 worker" in content
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd backend
uv run pytest tests/infrastructure/test_scripts.py -k stable_startup_docs -q
```

Expected: failure because the README does not yet describe either final health gate.

- [ ] **Step 3: Update operator documentation**

Add this paragraph under “数据库与容器”:

```markdown
`bash scripts/docker.sh up-workers` 每次都会从当前代码构建镜像并强制重建全部
容器，动态读取 Compose 完整服务清单，并在 180 秒内等待全部服务运行且健康；随后执行 Nginx 与后端
readiness smoke。服务退出、重启、不健康或超时会输出受影响服务的状态与最近
80 行日志并返回非零。`bash scripts/start-all.sh` 启动前停止旧实例、同步后端
锁定依赖并重建前端，同样在成功摘要前执行 `scripts/status.sh`，只有本地
MySQL、全部受管 worker、FastAPI、最新前端构建和 Nginx API/SPA 探针全部通过
才返回零。
```

- [ ] **Step 4: Run automated release gates**

Run:

```bash
cd backend
uv run pytest tests/infrastructure/test_scripts.py tests/infrastructure/test_compose.py -q
cd ..
bash -n scripts/lib/compose.sh scripts/docker.sh scripts/start-all.sh scripts/status.sh
bash scripts/docker.sh up-workers
bash scripts/docker.sh smoke
```

Expected: tests and syntax checks pass; the Compose startup command reports the configured service count healthy; `docker compose --profile workers ps` shows no unhealthy service.

- [ ] **Step 5: Verify the host startup without queue or port conflicts**

Run:

```bash
bash scripts/docker.sh down
bash scripts/start-all.sh
bash scripts/status.sh
bash scripts/stop-all.sh
bash scripts/docker.sh up-workers
```

Expected: the host startup reaches its final gate and reports success, the host stack stops cleanly, and the Compose stack returns to a fully healthy state.

- [ ] **Step 6: Commit and push**

```bash
git add scripts/lib/compose.sh scripts/lib/local_stack.sh scripts/start-all.sh scripts/stop-all.sh scripts/README.md backend/tests/infrastructure/test_scripts.py
git commit -m "feat(deploy): gate full startup on service health"
git push origin main
```

Expected: local `HEAD`, `origin/main`, and `git ls-remote origin refs/heads/main` match.
