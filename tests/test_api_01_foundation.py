import re
from pathlib import Path

import pytest


def declared_packages():
    source = Path(
        "requirements.txt"
    ).read_text(encoding="utf-8-sig")

    packages = set()

    for raw_line in source.splitlines():
        line = raw_line.strip()

        if (
            not line
            or line.startswith("#")
        ):
            continue

        package = re.split(
            r"[<>=!~\[]",
            line,
            maxsplit=1,
        )[0].strip().lower()

        packages.add(package)

    return packages


@pytest.mark.parametrize(
    "package",
    (
        "fastapi",
        "uvicorn",
    ),
)
def test_api_runtime_dependency_is_declared(
    package,
):
    assert package in declared_packages()


def create_test_client():
    import httpx

    from api.app import create_app

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(),
        ),
        base_url="http://testserver",
    )


def test_api_app_factory_uses_v1_paths():
    from api.app import create_app

    app = create_app()

    assert app.title == "SGHR API Platform"
    assert app.version == "1.0.0"
    assert app.openapi_url == "/api/v1/openapi.json"
    assert app.docs_url == "/api/v1/docs"
    assert app.redoc_url == "/api/v1/redoc"


@pytest.mark.asyncio
async def test_api_health_uses_success_envelope():
    request_id = "api-foundation-test"

    async with create_test_client() as client:
        response = await client.get(
            "/api/v1/health",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id
    assert response.json() == {
        "data": {
            "status": "ok",
            "service": "sghr-api",
            "api_version": "v1",
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_api_not_found_uses_error_envelope():
    request_id = "api-not-found-test"

    async with create_test_client() as client:
        response = await client.get(
            "/api/v1/missing",
            headers={
                "X-Request-ID": request_id,
            },
        )

    body = response.json()

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == request_id
    assert body == {
        "error": {
            "code": "not_found",
            "message": "Resource not found.",
            "request_id": request_id,
        },
    }


@pytest.mark.asyncio
async def test_api_generates_request_id():
    from uuid import UUID

    async with create_test_client() as client:
        response = await client.get(
            "/api/v1/health"
        )

    request_id = response.headers[
        "X-Request-ID"
    ]

    assert str(UUID(request_id)) == request_id
    assert (
        response.json()["request_id"]
        == request_id
    )


@pytest.mark.asyncio
async def test_unexpected_errors_are_sanitized():
    import httpx

    from api.app import create_app

    application = create_app()

    @application.get(
        "/api/v1/test-internal-error",
        include_in_schema=False,
    )
    async def raise_internal_error():
        raise RuntimeError(
            "private database details"
        )

    request_id = "api-internal-error-test"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/test-internal-error",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 500
    assert response.headers[
        "X-Request-ID"
    ] == request_id
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Internal server error.",
            "request_id": request_id,
        },
    }

    assert (
        "private database details"
        not in response.text
    )


def test_api_import_does_not_require_bot_token():
    import os
    import subprocess
    import sys

    environment = os.environ.copy()
    environment["BOT_TOKEN"] = ""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from api.app import create_app; "
                "assert create_app().title "
                "== 'SGHR API Platform'"
            ),
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        result.stdout + result.stderr
    )


@pytest.mark.asyncio
async def test_api_database_session_dependency(
    monkeypatch,
):
    import api.dependencies as dependencies

    fake_session = object()
    lifecycle = []

    class FakeSessionContext:
        async def __aenter__(self):
            lifecycle.append("enter")
            return fake_session

        async def __aexit__(
            self,
            exc_type,
            exc,
            traceback,
        ):
            lifecycle.append("exit")
            return False

    monkeypatch.setattr(
        dependencies,
        "get_session",
        lambda: FakeSessionContext(),
    )

    generator = (
        dependencies.get_api_session()
    )

    yielded_session = await anext(generator)

    assert yielded_session is fake_session
    assert lifecycle == ["enter"]

    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    assert lifecycle == [
        "enter",
        "exit",
    ]


def test_api_database_dependency_needs_no_bot_token():
    import os
    import subprocess
    import sys

    environment = os.environ.copy()
    environment["BOT_TOKEN"] = ""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from api.dependencies "
                "import get_api_session; "
                "assert callable(get_api_session)"
            ),
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        result.stdout + result.stderr
    )


def test_api_openapi_contains_only_versioned_routes():
    from api.app import create_app

    schema = create_app().openapi()

    assert schema["info"]["title"] == (
        "SGHR API Platform"
    )
    assert schema["info"]["version"] == "1.0.0"
    assert "/api/v1/health" in schema["paths"]

    assert all(
        route.startswith("/api/v1/")
        for route in schema["paths"]
    )


def test_api_layer_has_no_direct_business_access():
    import ast

    forbidden_imports = (
        "aiogram",
        "handlers",
        "database.repositories",
    )
    forbidden_transactions = {
        "commit",
        "flush",
        "rollback",
    }

    violations = []

    for api_path in sorted(
        Path("api").rglob("*.py")
    ):
        api_source = api_path.read_text(
            encoding="utf-8-sig"
        )
        tree = ast.parse(api_source)

        for node in ast.walk(tree):
            imported_modules = []

            if isinstance(node, ast.Import):
                imported_modules.extend(
                    alias.name
                    for alias in node.names
                )
            elif isinstance(
                node,
                ast.ImportFrom,
            ):
                imported_modules.append(
                    node.module or ""
                )

            for module in imported_modules:
                if module.startswith(
                    forbidden_imports
                ):
                    violations.append(
                        (
                            str(api_path),
                            node.lineno,
                            module,
                        )
                    )

            if (
                isinstance(node, ast.Call)
                and isinstance(
                    node.func,
                    ast.Attribute,
                )
                and node.func.attr
                in forbidden_transactions
            ):
                violations.append(
                    (
                        str(api_path),
                        node.lineno,
                        node.func.attr,
                    )
                )

    assert not violations, violations


def test_health_openapi_has_typed_envelope():
    from api.app import create_app

    schema = create_app().openapi()

    health_schema = (
        schema["paths"]["/api/v1/health"]
        ["get"]["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )

    assert health_schema == {
        "$ref": (
            "#/components/schemas/"
            "HealthResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "HealthResponse"
        ]["properties"]
    ) == {
        "data",
        "meta",
        "request_id",
    }

    assert set(
        components[
            "HealthData"
        ]["properties"]
    ) == {
        "status",
        "service",
        "api_version",
    }

    assert set(
        components[
            "ResponseMeta"
        ]["properties"]
    ) == set()


def test_api_systemd_service_contract():
    service_path = Path(
        "deploy/systemd/sghr-api.service"
    )

    assert service_path.exists()

    source = service_path.read_text(
        encoding="utf-8-sig"
    )

    required = (
        "Description=SGHR API Platform",
        "User=sghr",
        "Group=sghr",
        "WorkingDirectory=/opt/sghr",
        "EnvironmentFile=/opt/sghr/.env",
        (
            "ExecStart=/opt/sghr/venv/bin/"
            "uvicorn api.app:app"
        ),
        "--host 127.0.0.1",
        "--port 8000",
        "Restart=always",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "WantedBy=multi-user.target",
    )

    for value in required:
        assert value in source

    assert "--reload" not in source
    assert "bot.py" not in source


def test_api_deploy_runbook_contract():
    source = Path(
        "docs/deploy_runbook.md"
    ).read_text(encoding="utf-8-sig")

    required = (
        "sghr-api.service",
        "systemctl enable sghr-api",
        "systemctl start sghr-api",
        "systemctl restart sghr-api",
        "systemctl status sghr-api",
        "journalctl -u sghr-api",
        "/api/v1/health",
    )

    for value in required:
        assert value in source


def test_api_server_environment_contract():
    source = Path(
        "docs/server_env_checklist.md"
    ).read_text(encoding="utf-8-sig")

    required = (
        "/etc/systemd/system/sghr-api.service",
        "API_JWT_SECRET",
        "API_JWT_ISSUER",
        "API_JWT_AUDIENCE",
        "API_ACCESS_TOKEN_TTL_SECONDS",
        "systemctl start sghr-api",
        "systemctl status sghr-api",
        "/api/v1/health",
    )

    for value in required:
        assert value in source


@pytest.mark.asyncio
async def test_success_response_matches_api_contract():
    request_id = "api-response-contract"

    async with create_test_client() as client:
        response = await client.get(
            "/api/v1/health",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "status": "ok",
            "service": "sghr-api",
            "api_version": "v1",
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_error_response_matches_api_contract():
    request_id = "api-error-contract"

    async with create_test_client() as client:
        response = await client.get(
            "/api/v1/missing",
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Resource not found.",
            "request_id": request_id,
        },
    }

