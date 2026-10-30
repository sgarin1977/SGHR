"""Public shell tests. Authentication and mutation stay in existing APIs."""
import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

@pytest.fixture
def page_app():
    source = Path(__file__).resolve().parents[2] / "api/routes/construction_client_link_page.py"
    spec = importlib.util.spec_from_file_location("client_link_page_route", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router)
    return app

@pytest.mark.asyncio
@pytest.mark.parametrize("suffix,media", [
    ("", "text/html"), ("/style.css", "text/css"),
    ("/flow.mjs", "text/javascript"), ("/boot.mjs", "text/javascript"),
])
async def test_assets_require_no_identity_or_database(page_app, suffix, media):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=page_app), base_url="https://test",
    ) as client:
        result = await client.get("/construction/client-user-link/confirm" + suffix)
    assert result.status_code == 200
    assert result.headers["content-type"].startswith(media)
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["referrer-policy"] == "no-referrer"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in result.headers["content-security-policy"]
    assert "connect-src 'self'" in result.headers["content-security-policy"]

@pytest.mark.asyncio
async def test_no_query_credentials_reflection_or_directory_mount(page_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=page_app), base_url="https://test",
    ) as client:
        invalid = await client.get(
            "/construction/client-user-link/confirm?token=private-secret",
        )
        missing = await client.get("/construction/client-user-link/confirm/unknown.txt")
    assert invalid.status_code == 422 and "private-secret" not in invalid.text
    assert missing.status_code == 404

def test_shell_registration_and_local_assets():
    root = Path(__file__).resolve().parents[2]
    source = (root / "api/app.py").read_text(encoding="utf-8-sig")
    assert "application.include_router(construction_client_link_page_router)" in source
    html = (root / "ui/web/client_link_confirmation/index.html").read_text(encoding="utf-8")
    assert '<script>' not in html and "https://" not in html
    assert 'type="module" src="/construction/client-user-link/confirm/boot.mjs"' in html

# client_link_confirmation_page_v1
