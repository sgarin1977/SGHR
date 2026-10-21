import importlib.util
from pathlib import Path
import httpx
import pytest
from fastapi import FastAPI

@pytest.fixture
def page_app():
    source = Path(__file__).resolve().parents[1]/'api/routes/construction_invitation_page.py'
    spec=importlib.util.spec_from_file_location('recipient_page_route',source)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    app=FastAPI(); app.include_router(module.router)
    return app

@pytest.mark.asyncio
@pytest.mark.parametrize('suffix,media',[('', 'text/html'),('/style.css','text/css'),('/flow.mjs','text/javascript'),('/boot.mjs','text/javascript')])
async def test_public_assets_without_auth_or_database(page_app,suffix,media):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=page_app),base_url='https://test') as client:
        response=await client.get('/construction/invitations/recipient'+suffix)
    assert response.status_code==200 and response.headers['content-type'].startswith(media)
    assert response.headers['referrer-policy']=='no-referrer'
    assert response.headers['x-content-type-options']=='nosniff'
    assert response.headers['cache-control']=='no-store'
    csp=response.headers['content-security-policy']
    assert "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp

@pytest.mark.asyncio
async def test_query_credentials_not_reflected_and_no_directory_mount(page_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=page_app),base_url='https://test') as client:
        bad=await client.get('/construction/invitations/recipient?token=private-secret')
        missing=await client.get('/construction/invitations/recipient/unknown.txt')
    assert bad.status_code==422 and 'private-secret' not in bad.text
    assert missing.status_code==404

def test_shell_uses_local_assets_and_no_inline_script(page_app):
    root=Path(__file__).resolve().parents[1]
    html=(root/'ui/web/invitation_recipient/index.html').read_text(encoding='utf-8')
    assert 'type="module" src="/construction/invitations/recipient/boot.mjs"' in html
    assert '<script>' not in html and 'https://' not in html
    source=(root/'api/app.py').read_text(encoding='utf-8')
    assert 'application.include_router(construction_invitation_page_router)' in source
