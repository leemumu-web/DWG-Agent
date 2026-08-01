"""Verify compose.yaml is valid YAML and has expected defensive defaults.

These are lightweight static checks — no Docker daemon required.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from tests.support.paths import REPO_ROOT

COMPOSE_PATH = REPO_ROOT / "compose.yaml"
DEV_COMPOSE_PATH = REPO_ROOT / "compose.dev.yaml"
DOCKERFILE_PATH = REPO_ROOT / "backend" / "Dockerfile"
FRONTEND_DOCKERFILE_PATH = REPO_ROOT / "frontend" / "Dockerfile"
BACKEND_PYPROJECT_PATH = REPO_ROOT / "backend" / "pyproject.toml"
DOCKERIGNORE_PATH = REPO_ROOT / ".dockerignore"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"
DOCKER_ENV_EXAMPLE_PATH = REPO_ROOT / ".env.docker.example"
STORAGE_PROBE_PATH = REPO_ROOT / "scripts" / "storage" / "verify_transactions.py"
APP_SECRET_KEYS = {
    "JWT_SECRET_KEY",
    "SUPER_ADMIN_PASSWORD",
    "DATABASE_URL",
}
APP_SERVICE_NAMES = (
    "backend-api",
    "dispatcher",
    "worker-dxf",
    "worker-dxf2dwg",
    "worker-dxf2excel",
    "worker-dxf-classification",
    "worker-dxf-split",
    "worker-excel-final",
    "worker-excel-stage2",
    "worker-report",
    "worker-remnant-convert",
    "worker-remnant-parse",
)


def _load():
    with open(COMPOSE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_dev():
    with open(DEV_COMPOSE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _assert_blank_environment(service: dict, keys: set[str]) -> None:
    env = service.get("environment", {})
    for key in keys:
        assert env.get(key) == "", f"{key} should be scrubbed from {service}"


class TestAppServices:
    def test_app_services_use_docker_env_file_and_hide_root_passwords(self):
        data = _load()
        for name in APP_SERVICE_NAMES:
            service = data["services"][name]
            assert service["env_file"] == [".env.docker"]
            assert service["environment"]["MYSQL_ROOT_PASSWORD"] == ""
            assert service["environment"]["MINIO_ROOT_PASSWORD"] == ""

    def test_worker_services_use_process_healthchecks_without_remote_control(self):
        data = _load()
        for service_name in APP_SERVICE_NAMES:
            if not service_name.startswith("worker-"):
                continue
            command = " ".join(data["services"][service_name]["healthcheck"]["test"])
            assert "/tmp/dwg-celery-ready" in command
            assert "inspect" not in command
            assert "localhost:8010/health" not in command
            if service_name in {"worker-dxf", "worker-dxf2dwg"}:
                expected_queue = "dxf2dwg" if service_name.endswith("dxf2dwg") else "dxf"
                assert f"/tmp/dwg-celery-{expected_queue}.pid" in command
                assert "kill -0" in command
            else:
                assert "/proc/1/cmdline" in command

    def test_dispatcher_is_independent_bounded_and_waits_for_schema_migration(self):
        dispatcher = _load()["services"]["dispatcher"]

        assert dispatcher["command"] == ["python", "-m", "app.modules.jobs.dispatcher"]
        assert dispatcher["depends_on"] == {"backend-api": {"condition": "service_healthy"}}
        assert dispatcher["cpus"] == "${DISPATCHER_CPU_LIMIT:-0.5}"
        assert dispatcher["mem_limit"] == "${DISPATCHER_MEMORY_LIMIT:-512m}"
        assert dispatcher["pids_limit"] == "${DISPATCHER_PIDS_LIMIT:-64}"
        health = " ".join(dispatcher["healthcheck"]["test"])
        assert "app.modules.jobs.dispatcher" in health

    def test_unsupported_flower_service_is_absent(self):
        data = _load()
        assert "flower" not in data["services"]

    def test_contract_only_queues_do_not_run_zombie_workers(self):
        services = _load()["services"]

        for queue in ("agent", "cad", "dispatch"):
            assert f"worker-{queue}" not in services

    def test_stage_dependencies_and_standalone_excel_runner_are_copied_into_image(self):
        dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

        for stage in ("dwg2dxf", "dxf2dwg", "dxf2excel"):
            assert f"COPY Stages/{stage} ./Stages/{stage}" in dockerfile
        assert (
            "COPY Stages/steel_dxf_classifier_v1.1.0 ./Stages/steel_dxf_classifier_v1.1.0"
        ) in dockerfile
        assert ("COPY Stages/steel_dxf_split_v1.5.2 ./Stages/steel_dxf_split_v1.5.2") in dockerfile
        assert "COPY Stages/excel_final /app/Stages/excel_final" in dockerfile
        assert "COPY scripts/run-worker.sh /app/scripts/run-worker.sh" in dockerfile
        assert "COPY scripts/run-cad-worker.sh /app/scripts/run-cad-worker.sh" in dockerfile
        assert "COPY scripts/lib/cad_worker.sh /app/scripts/lib/cad_worker.sh" in dockerfile
        assert "COPY scripts/lib/local_stack.sh /app/scripts/lib/local_stack.sh" in dockerfile
        assert "COPY scripts/lib/common.sh /app/scripts/lib/common.sh" in dockerfile

    def test_bh_reader_is_version_locked_path_dependency_and_copied_into_image(self):
        pyproject = BACKEND_PYPROJECT_PATH.read_text(encoding="utf-8")
        dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
        reader_root = REPO_ROOT / "Stages" / "bh_left_right_reader"

        assert reader_root.is_dir()
        assert '"bh-left-right-reader==1.2.7"' in pyproject
        assert (
            'bh-left-right-reader = { path = "../Stages/bh_left_right_reader", editable = true }'
        ) in pyproject
        assert ("COPY Stages/bh_left_right_reader ./Stages/bh_left_right_reader") in dockerfile
        assert (
            "./Stages/bh_left_right_reader:/app/Stages/bh_left_right_reader"
            in _load_dev()["services"]["backend-api"]["volumes"]
        )

    def test_bh_reader_delivered_source_matches_the_locked_manifest(self):
        reader_root = REPO_ROOT / "Stages" / "bh_left_right_reader"
        manifest_path = reader_root / "SOURCE_MANIFEST.sha256"

        assert manifest_path.is_file()
        entries = [
            line.split(maxsplit=1)
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        ]
        assert entries
        for expected_sha256, relative_name in entries:
            source_path = reader_root / relative_name
            assert source_path.is_file(), relative_name
            assert hashlib.sha256(source_path.read_bytes()).hexdigest() == expected_sha256

    def test_conversion_workers_use_persistent_xvfb_and_configurable_concurrency(self):
        data = _load()
        expected = {
            "worker-dxf": ("dxf", "${DXF_WORKER_CONCURRENCY:-8}", "${DXF_WORKER_DISPLAY:-:91}"),
            "worker-dxf2dwg": (
                "dxf2dwg",
                "${DXF2DWG_WORKER_CONCURRENCY:-8}",
                "${DXF2DWG_WORKER_DISPLAY:-:92}",
            ),
        }

        for service_name, (queue, concurrency, display) in expected.items():
            service = data["services"][service_name]
            command = service["command"]
            assert command[0] == "/app/scripts/run-worker.sh"
            assert command[1] == "/app/scripts/run-cad-worker.sh"
            assert command[2] == queue
            assert command[3] == concurrency
            assert command[5] == display
            health = " ".join(service["healthcheck"]["test"])
            assert f"/tmp/dwg-celery-{queue}.pid" in health

    def test_conversion_workers_extract_appimage_outside_hardened_tmpfs(self):
        data = _load()
        worker_script = (REPO_ROOT / "scripts" / "lib" / "cad_worker.sh").read_text(
            encoding="utf-8"
        )

        for service_name in ("worker-dxf", "worker-dxf2dwg"):
            service = data["services"][service_name]
            assert service["environment"]["TMPDIR"] == "/app/var/appimage-tmp"
            assert "app_var:/app/var" in service["volumes"]

        assert 'mkdir -p "$TMPDIR"' in worker_script

    def test_classification_worker_uses_configurable_fixed_pool(self):
        service = _load()["services"]["worker-dxf-classification"]
        command = service["command"]

        assert ("--concurrency=${DXF_CLASSIFICATION_WORKER_CONCURRENCY:-3}") in command
        assert not any("--autoscale" in argument for argument in command)
        assert (
            service["environment"]["DWG_WORKER_CONCURRENCY"]
            == "${DXF_CLASSIFICATION_WORKER_CONCURRENCY:-3}"
        )
        assert "DWG_WORKER_AUTOSCALE" not in service["environment"]

    def test_api_and_independent_job_workers_have_configurable_concurrency(self):
        data = _load()
        dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
        env_example = DOCKER_ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

        assert "--workers ${WEB_CONCURRENCY:-4}" in dockerfile
        expected = {
            "worker-report": ("REPORT_WORKER_CONCURRENCY", "1"),
            "worker-dxf-split": ("DXF_SPLIT_WORKER_CONCURRENCY", "1"),
            "worker-excel-final": ("EXCEL_FINAL_WORKER_CONCURRENCY", "1"),
            "worker-excel-stage2": ("EXCEL_STAGE2_WORKER_CONCURRENCY", "1"),
        }
        for service_name, (variable, default) in expected.items():
            service = data["services"][service_name]
            command = service["command"]
            assert f"--concurrency=${{{variable}:-{default}}}" in command
            assert service["environment"]["DWG_WORKER_CONCURRENCY"] == f"${{{variable}:-{default}}}"
            assert f"{variable}={default}" in env_example
        assert "WEB_CONCURRENCY=4" in env_example

    def test_long_lived_containers_raise_open_file_limit(self):
        data = _load()
        expected = {"soft": 65536, "hard": 65536}

        for service_name, service in data["services"].items():
            assert service.get("ulimits", {}).get("nofile") == expected, (
                f"{service_name} must tolerate concurrent uploads and downloads"
            )

    def test_mysql_capacity_is_configurable_without_exposing_database_port(self):
        data = _load()
        mysql = data["services"]["mysql"]
        command = mysql["command"]
        env_example = DOCKER_ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

        assert "--innodb-buffer-pool-size=${MYSQL_INNODB_BUFFER_POOL_SIZE:-2G}" in command
        assert "--max-connections=${MYSQL_MAX_CONNECTIONS:-200}" in command
        assert "--table-open-cache=${MYSQL_TABLE_OPEN_CACHE:-2000}" in command
        assert "--thread-cache-size=${MYSQL_THREAD_CACHE_SIZE:-50}" in command
        assert "ports" not in mysql
        for setting in (
            "MYSQL_INNODB_BUFFER_POOL_SIZE=2G",
            "MYSQL_MAX_CONNECTIONS=200",
            "MYSQL_TABLE_OPEN_CACHE=2000",
            "MYSQL_THREAD_CACHE_SIZE=50",
        ):
            assert setting in env_example

    def test_minio_bypasses_incompatible_glibc_entrypoint_and_uses_static_healthcheck(
        self,
    ):
        minio = _load()["services"]["minio"]

        assert minio["entrypoint"] == ["/usr/bin/minio"]
        assert minio["healthcheck"]["test"] == ["CMD", "/usr/bin/mc", "ready", "local"]
        assert (
            minio["environment"]["MC_HOST_local"]
            == "http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@127.0.0.1:9000"
        )
        assert "curl" not in " ".join(minio["healthcheck"]["test"])


class TestComposeYamlValid:
    def test_is_parseable_yaml(self):
        assert _load() is not None

    def test_every_container_runs_with_beijing_timezone_data(self):
        data = _load()
        env_example = DOCKER_ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        backend_dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
        frontend_dockerfile = FRONTEND_DOCKERFILE_PATH.read_text(encoding="utf-8")

        assert "TZ=Asia/Shanghai" in env_example
        assert "--default-time-zone=+08:00" in data["services"]["mysql"]["command"]
        assert data["services"]["nginx"]["environment"]["TZ"] == "Asia/Shanghai"
        assert "tzdata" in backend_dockerfile
        assert "apk add --no-cache tzdata" in frontend_dockerfile
        for name, service in data["services"].items():
            if name == "nginx":
                continue
            assert service.get("env_file") == [".env.docker"], name

    def test_frontend_builder_uses_one_pinned_non_docker_hub_node_image(self):
        node_image = (
            "public.ecr.aws/docker/library/node:22-alpine@sha256:"
            "c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32"
        )
        data = _load()
        dockerfile = FRONTEND_DOCKERFILE_PATH.read_text(encoding="utf-8")
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        docker_env_example = DOCKER_ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

        assert f"ARG NODE_IMAGE={node_image}" in dockerfile
        assert data["services"]["nginx"]["build"]["args"]["NODE_IMAGE"] == (
            "${NODE_IMAGE:-" + node_image + "}"
        )
        assert f"NODE_IMAGE={node_image}" in env_example
        assert f"NODE_IMAGE={node_image}" in docker_env_example

    def test_has_expected_services(self):
        data = _load()
        actual = set(data.get("services", {}))
        required = {
            "nginx",
            "backend-api",
            "dispatcher",
            "worker-dxf",
            "worker-dxf2dwg",
            "worker-dxf2excel",
            "worker-dxf-classification",
            "worker-dxf-split",
            "worker-excel-final",
            "worker-excel-stage2",
            "worker-report",
            "worker-remnant-convert",
            "worker-remnant-parse",
            "mysql",
            "minio",
        }
        assert required <= actual, f"Missing services: {sorted(required - actual)}"

    def test_production_volumes_have_stable_names(self):
        volumes = _load()["volumes"]

        assert volumes == {
            "app_var": {"name": "dwg-agent_app_var"},
            "mysql_data": {"name": "dwg-agent_mysql_data"},
            "minio_data": {"name": "dwg-agent_minio_data"},
        }

    def test_every_long_lived_service_has_bounded_json_logs(self):
        services = _load()["services"]

        for name, service in services.items():
            assert service.get("logging") == {
                "driver": "json-file",
                "options": {"max-size": "20m", "max-file": "5"},
            }, f"{name} must not write unbounded Docker logs"

    def test_core_infra_images_do_not_depend_on_docker_hub(self):
        data = _load()
        services = data["services"]

        assert services["nginx"]["build"]["dockerfile"] == "frontend/Dockerfile"
        assert "dwg-agent-frontend:local" in services["nginx"]["image"]
        assert (
            "public.ecr.aws/docker/library/mysql@sha256:"
            "224bcb427d70a54ea2a5c8f47dfcf697ae4baefedd02ecf8d4229dd7b061293d"
            in services["mysql"]["image"]
        )
        assert (
            "quay.io/minio/minio@sha256:"
            "1dce27c494a16bae114774f1cec295493f3613142713130c2d22dd5696be6ad3"
            in services["minio"]["image"]
        )
        assert ":latest" not in services["minio"]["image"]
        assert (
            "${HTTP_BIND_ADDRESS:-0.0.0.0}:${HTTP_PORT:-80}:8080"
            in services["nginx"]["ports"]
        )

        nginx_conf = (REPO_ROOT / "infra/gateway/nginx/nginx.conf").read_text(encoding="utf-8")
        assert "listen 8080;" in nginx_conf
        assert "listen 80;" not in nginx_conf

    def test_docker_nginx_conf_uses_unprivileged_runtime_paths(self):
        nginx_conf = (REPO_ROOT / "infra/gateway/nginx/nginx.conf").read_text(encoding="utf-8")

        assert "/var/log/nginx" not in nginx_conf
        assert "error_log /dev/stderr warn;" in nginx_conf
        assert "access_log /dev/stdout extended;" in nginx_conf
        assert "pid /tmp/nginx.pid;" in nginx_conf
        for temp_path in (
            "proxy_temp_path /tmp/proxy_temp;",
            "client_body_temp_path /tmp/client_temp;",
            "fastcgi_temp_path /tmp/fastcgi_temp;",
            "uwsgi_temp_path /tmp/uwsgi_temp;",
            "scgi_temp_path /tmp/scgi_temp;",
        ):
            assert temp_path in nginx_conf


class TestDevelopmentCompose:
    def test_dev_override_is_parseable_and_uses_current_backend_port(self):
        data = _load_dev()
        backend = data["services"]["backend-api"]

        assert "uvicorn app.main:app" in backend["command"]
        assert "--reload" in backend["command"]
        assert "--port 8010" in backend["command"]
        assert backend["ports"] == ["127.0.0.1:8010:8010"]

    def test_dev_override_mounts_backend_source_into_every_implemented_worker(self):
        data = _load_dev()
        workers = (
            "dispatcher",
            "worker-report",
            "worker-dxf",
            "worker-dxf2dwg",
            "worker-dxf2excel",
            "worker-dxf-classification",
            "worker-dxf-split",
            "worker-excel-final",
            "worker-excel-stage2",
        )
        for worker in workers:
            volumes = data["services"][worker]["volumes"]
            assert "./backend/app:/app/app" in volumes

        assert "./Stages/dwg2dxf:/app/Stages/dwg2dxf" in data["services"]["worker-dxf"]["volumes"]
        assert (
            "./Stages/dxf2dwg:/app/Stages/dxf2dwg" in data["services"]["worker-dxf2dwg"]["volumes"]
        )
        assert (
            "./Stages/dxf2excel:/app/Stages/dxf2excel"
            in data["services"]["worker-dxf2excel"]["volumes"]
        )
        assert (
            "./Stages/steel_dxf_classifier_v1.1.0:/app/Stages/steel_dxf_classifier_v1.1.0"
        ) in data["services"]["worker-dxf-classification"]["volumes"]
        assert ("./Stages/steel_dxf_split_v1.5.2:/app/Stages/steel_dxf_split_v1.5.2") in data[
            "services"
        ]["worker-dxf-split"]["volumes"]
        assert (
            "./Stages/excel_final:/app/Stages/excel_final"
            in data["services"]["worker-excel-final"]["volumes"]
        )
        stage2_volumes = data["services"]["worker-excel-stage2"]["volumes"]
        assert "./Stages/excel_final:/app/Stages/excel_final" in stage2_volumes
        assert "./Stages/bh_left_right_reader:/app/Stages/bh_left_right_reader" in stage2_volumes

    def test_dev_override_does_not_publish_mysql_or_minio(self):
        services = _load_dev()["services"]

        assert "mysql" not in services or "ports" not in services["mysql"]
        assert "minio" not in services or "ports" not in services["minio"]

    def test_dev_override_keeps_persistent_data_isolated_from_production(self):
        volumes = _load_dev()["volumes"]

        assert volumes == {
            "app_var": {"name": "dwg-agent-dev_app_var"},
            "mysql_data": {"name": "dwg-agent-dev_mysql_data"},
            "minio_data": {"name": "dwg-agent-dev_minio_data"},
        }


class TestMysqlService:
    def test_mysql_uses_docker_env_file_and_scrubs_app_secrets(self):
        data = _load()
        mysql = data["services"]["mysql"]
        assert mysql["env_file"] == [".env.docker"]
        _assert_blank_environment(
            mysql,
            APP_SECRET_KEYS | {"MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"},
        )
        assert "MYSQL_PASSWORD" not in mysql["environment"]
        assert "MYSQL_ROOT_PASSWORD" not in mysql["environment"]

    def test_mysql_volumes_include_init_sql(self):
        data = _load()
        volumes = data["services"]["mysql"]["volumes"]
        init_mounts = [v for v in volumes if "init.sql" in str(v)]
        assert len(init_mounts) >= 1, "init.sql should be mounted"

    def test_mysql_initializes_hardware_handbook_after_platform_grants(self):
        data = _load()
        volumes = data["services"]["mysql"]["volumes"]

        assert any("01-platform.sql" in str(volume) for volume in volumes)
        assert any("02-hardware-handbook.sql" in str(volume) for volume in volumes)
        init_sql = (REPO_ROOT / "infra/database/mysql/init.sql").read_text(encoding="utf-8")
        assert "GRANT SELECT ON hardware_handbook.*" in init_sql

    def test_mysql_has_healthcheck(self):
        data = _load()
        hc = data["services"]["mysql"]["healthcheck"]
        test_cmd = " ".join(hc["test"])
        assert "mysqladmin" not in test_cmd
        assert "env -u MYSQL_HOST -u MYSQL_PORT mysql" in test_cmd
        assert "--protocol=TCP" in test_cmd
        assert "-h 127.0.0.1" in test_cmd
        assert "MYSQL_UNIX_PORT" not in test_cmd
        assert "SELECT 1" in test_cmd
        assert "$${MYSQL_ROOT_PASSWORD}" in test_cmd
        assert "${MYSQL_ROOT_PASSWORD:-" not in test_cmd


class TestMinioService:
    def test_minio_uses_docker_env_file_and_scrubs_unrelated_secrets(self):
        data = _load()
        minio = data["services"]["minio"]
        assert minio["env_file"] == [".env.docker"]
        _assert_blank_environment(
            minio,
            APP_SECRET_KEYS
            | {
                "MYSQL_PASSWORD",
                "MYSQL_ROOT_PASSWORD",
                "MINIO_ACCESS_KEY",
                "MINIO_SECRET_KEY",
            },
        )
        assert "MINIO_ROOT_PASSWORD" not in minio["environment"]

    def test_minio_exposes_read_only_metrics_only_on_internal_network(self):
        data = _load()
        minio = data["services"]["minio"]

        assert minio["environment"]["MINIO_PROMETHEUS_AUTH_TYPE"] == "public"
        assert minio["networks"] == ["internal"]
        assert "ports" not in minio

        docker_env = DOCKER_ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        assert "MINIO_METRICS_URL=http://minio:9000/minio/v2/metrics/cluster" in docker_env


class TestClassifiedInfrastructureLayout:
    def test_runtime_assets_live_under_explicit_owners(self):
        for relative in (
            "infra/gateway/nginx/nginx.conf",
            "infra/database/mysql/init.sql",
            "infra/storage/minio",
            "infra/verification/verify.sh",
        ):
            assert (REPO_ROOT / relative).exists(), relative

    def test_rabbitmq_target_is_truthful_and_not_silently_deployed(self):
        data = _load()
        readme = (REPO_ROOT / "infra/messaging/rabbitmq/README.md").read_text(encoding="utf-8")

        assert "rabbitmq" not in data["services"]
        assert "Status: target contract, not deployed in current Compose." in readme
        assert "MySQL SQLAlchemy Celery transport" in readme

    def test_windows_boundary_is_split_by_process_role(self):
        for relative in (
            "windows/node-agent/README.md",
            "windows/cam-runner/README.md",
            "windows/sinocam-adapter/README.md",
            "windows/protocols/README.md",
        ):
            assert (REPO_ROOT / relative).is_file(), relative

        assert not (REPO_ROOT / "cad-worker").exists()

    def test_root_logo_duplicate_is_removed(self):
        assert (REPO_ROOT / "frontend/public/logo.png").is_file()
        assert not (REPO_ROOT / "image.png").exists()


class TestDockerEnvironmentFiles:
    def _env_keys(self, path: Path) -> set[str]:
        return {
            line.split("=", 1)[0]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and "=" in line
        }

    def test_docker_env_example_exists(self):
        assert DOCKER_ENV_EXAMPLE_PATH.exists(), ".env.docker.example must be committed"

    def test_env_examples_have_same_keys(self):
        local_keys = self._env_keys(REPO_ROOT / ".env.example")
        docker_keys = self._env_keys(DOCKER_ENV_EXAMPLE_PATH)
        assert docker_keys == local_keys

    def test_docker_env_example_has_no_nested_compose_interpolation(self):
        content = DOCKER_ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        assert "${" not in content, (
            "env_file values must not depend on shell/root .env interpolation"
        )
        assert "mysql:3306" in content
        assert "http://minio:9000" in content

    def test_local_docker_env_is_gitignored(self):
        content = GITIGNORE_PATH.read_text(encoding="utf-8")
        assert ".env.docker" in content

    def test_server_example_enables_approved_shipping_pipelines(self):
        values = {
            line.split("=", 1)[0]: line.split("=", 1)[1]
            for line in DOCKER_ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and "=" in line
        }

        for key in (
            "DXF_PIPELINE_ENABLED",
            "DXF2DWG_PIPELINE_ENABLED",
            "DXF_CLASSIFICATION_PIPELINE_ENABLED",
            "DXF_SPLIT_PIPELINE_ENABLED",
            "EXCEL_FINAL_PIPELINE_ENABLED",
            "REMNANT_INVENTORY_ENABLED",
        ):
            assert values[key] == "true"
        assert values["DXF2EXCEL_PIPELINE_ENABLED"] == "false"


# ── Dockerfile static checks ──────────────────────────────────────


class TestDockerfile:
    def test_exists(self):
        assert DOCKERFILE_PATH.exists(), "Dockerfile missing"

    def test_storage_transaction_probe_is_available_in_runtime_image(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")

        assert (
            "COPY scripts/storage/verify_transactions.py "
            "/app/scripts/storage/verify_transactions.py"
        ) in content

    def test_runtime_feature_probe_is_available_in_protected_image(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")

        assert (
            "COPY scripts/release/verify_runtime_features.py "
            "/app/scripts/release/verify_runtime_features.py"
        ) in content

    def test_storage_probe_bootstraps_packaged_app_before_importing_it(self):
        content = STORAGE_PROBE_PATH.read_text(encoding="utf-8")

        bootstrap = "sys.path.insert(0, str(Path(__file__).resolve().parents[2]))"
        assert bootstrap in content
        assert content.index(bootstrap) < content.index("from app.main import app")

    def test_storage_probe_is_independent_of_optional_excel_feature_flag(self):
        content = STORAGE_PROBE_PATH.read_text(encoding="utf-8")

        assert 'patch.object(settings, "excel_final_pipeline_enabled", True)' in content

    def test_is_multi_stage(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        stages = [line for line in content.splitlines() if line.startswith("FROM ")]
        assert len(stages) >= 2, "Dockerfile should use multi-stage build"

    def test_uses_buildable_python_uv_base_image(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")

        assert "FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder" in content
        assert "FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime" in content
        assert "FROM python:3.12-slim" not in content

    def test_does_not_copy_uv_from_latest_image(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")

        assert "ghcr.io/astral-sh/uv:latest" not in content

    def test_copies_packaging_readme_before_uv_sync(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")

        assert "COPY backend/pyproject.toml backend/uv.lock backend/README.md ./backend/" in content

    def test_has_non_root_user(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        assert "USER appuser" in content, "Must run as non-root user (spec §17.5-4)"
        assert "useradd" in content or "adduser" in content, "Must create appuser"
        assert "ENV HOME=/home/appuser" in content
        assert "mkdir -p /app/var /home/appuser" in content

    def test_runtime_runs_alembic_before_gunicorn(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        command = content.split('CMD ["sh", "-c",', maxsplit=1)[1]
        assert command.index("python -m app.platform.database.wait") < command.index(
            "alembic upgrade head"
        )
        assert command.index("alembic upgrade head") < command.index("python -m app.bootstrap.seed")
        assert command.index("python -m app.bootstrap.seed") < command.index("exec gunicorn")

    def test_has_healthcheck(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        assert "HEALTHCHECK" in content, "Dockerfile should have HEALTHCHECK"

    def test_runtime_installs_xauth_required_by_xvfb_run(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        runtime = content.split(" AS runtime", 1)[1]

        assert "xvfb" in runtime
        assert "xauth" in runtime

    def test_runtime_can_execute_oda_appimage_without_host_fuse(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        runtime = content.split(" AS runtime", 1)[1]

        assert "ENV APPIMAGE_EXTRACT_AND_RUN=1" in runtime
        assert "libfontconfig1" in runtime

    def test_runtime_installs_noto_cjk_font_package(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        runtime = content.split(" AS runtime", 1)[1]

        assert "fonts-noto-cjk" in runtime

    def test_does_not_copy_env_file(self):
        """Spec §17.5-3: .env must not be baked into image."""
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        assert ".env" not in content, "Must not COPY .env into image"


class TestDockerignore:
    def test_exists(self):
        assert DOCKERIGNORE_PATH.exists(), ".dockerignore missing"

    def test_excludes_tests(self):
        content = DOCKERIGNORE_PATH.read_text(encoding="utf-8")
        assert "tests/" in content, ".dockerignore should exclude tests/"

    def test_excludes_env(self):
        content = DOCKERIGNORE_PATH.read_text(encoding="utf-8")
        assert ".env" in content, ".dockerignore should exclude .env"

    def test_excludes_venv_and_cache(self):
        content = DOCKERIGNORE_PATH.read_text(encoding="utf-8")
        assert ".venv/" in content
        assert "__pycache__" in content

    def test_excludes_large_assets_not_used_by_backend_image(self):
        content = DOCKERIGNORE_PATH.read_text(encoding="utf-8")
        for path in (
            "third_parts/",
            "Stages/dxf2excel/original_dxf/",
            "Stages/dwg2dxf/convert/",
            "Stages/dxf2dwg/tools/oda/",
            "Stages/steel_dxf_split_v1.5.2/samples/",
            "Stages/steel_dxf_split_v1.5.2/tests/",
        ):
            assert path in content
