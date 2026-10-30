"""Public UI assets; invitation authority remains in the existing authenticated APIs."""
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import Response

ASSETS = Path(__file__).resolve().parents[2] / 'ui/web/invitation_recipient'
PREFIX = '/construction/invitations/recipient'
HEADERS = {
    'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer', 'X-Content-Type-Options':'nosniff',
    'X-Frame-Options':'DENY', 'Permissions-Policy':'camera=(), microphone=(), geolocation=()',
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'",
}
router = APIRouter(include_in_schema=False)

def asset(name, media_type):
    return Response(content=(ASSETS/name).read_bytes(), media_type=media_type, headers=HEADERS)

@router.get(PREFIX)
def invitation_recipient_page(request: Request):
    if request.query_params:
        return Response('Invitation links must use a URL fragment.', status_code=422, headers=HEADERS)
    return asset('index.html','text/html')

@router.get(PREFIX+'/style.css')
def invitation_recipient_style(): return asset('style.css','text/css')

@router.get(PREFIX+'/flow.mjs')
def invitation_recipient_flow(): return asset('flow.mjs','text/javascript')

@router.get(PREFIX+'/boot.mjs')
def invitation_recipient_boot(): return asset('boot.mjs','text/javascript')

@router.get(PREFIX+'/telegram-binding.mjs')
def invitation_recipient_telegram_binding(): return asset('telegram-binding.mjs','text/javascript')

# invitation_binding_session_v1
