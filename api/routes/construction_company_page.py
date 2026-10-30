"""Company UI shell and read-only capability projection of existing policies."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from api.auth import get_current_actor, require_module, require_suite, require_authenticated_user_rate_limit
from api.responses import success_envelope
from api.routes.construction import get_construction_access_context
from services.platform_company_profile_access import resolve_company_profile_permissions, PLATFORM_ORGANIZATION_PROFILE_MANAGE
from services.construction.construction_tenant_settings import require_construction_settings_manage, ConstructionTenantSettingsPermissionDeniedError
from services.files import IMAGE_MAX_SIZE_BYTES, _FILE_TYPES_BY_EXTENSION

ASSETS=Path(__file__).resolve().parents[2]/'ui/web/company_settings'
PREFIX='/construction/settings/company'
def storage_origin():
    raw=os.environ.get('SUPABASE_URL','')
    try:
        parsed=urlsplit(raw)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ('','/') or any(c.isspace() for c in raw)
                or not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]{1,5})?',parsed.netloc)):
            return None
        parsed.port
        return 'https://'+parsed.netloc
    except ValueError:return None

def capabilities(actor,context):
    profile=PLATFORM_ORGANIZATION_PROFILE_MANAGE in resolve_company_profile_permissions(actor=actor,construction_access_context=context)
    try:require_construction_settings_manage(actor=actor,access_context=context);settings=True
    except ConstructionTenantSettingsPermissionDeniedError:settings=False
    return {'tenant_id':str(actor.tenant_id),'can_manage_profile':profile,'can_manage_settings':settings,
        'storage_origin':storage_origin(),'image_max_size_bytes':IMAGE_MAX_SIZE_BYTES,
        'image_types':[{'extension':extension,'mime_type':mime} for extension,(kind,mime) in _FILE_TYPES_BY_EXTENSION.items() if kind=='image']}

api_router=APIRouter(prefix='/construction/ui',dependencies=[Depends(get_current_actor),Depends(require_authenticated_user_rate_limit()),
    Depends(require_suite('construction',error_code='MODULE_DISABLED')),Depends(require_module('construction.core',error_code='MODULE_DISABLED'))])
@api_router.get('/company-capabilities')
async def company_capabilities(request:Request,response:Response,actor=Depends(get_current_actor),context=Depends(get_construction_access_context)):
    response.headers['Cache-Control']='private, no-store'
    return success_envelope(data=capabilities(actor,context),request_id=request.state.request_id)

def headers():
    origin=storage_origin()
    return {'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
        'Permissions-Policy':'camera=(), microphone=(), geolocation=()',
        'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'"+(' '+origin if origin else '')+"; img-src 'self' blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"}
def asset(name,media):return Response((ASSETS/name).read_bytes(),media_type=media,headers=headers())
router=APIRouter(include_in_schema=False)
@router.get(PREFIX)
def company_page(request:Request):
    if request.query_params:return Response('Query parameters are not supported.',status_code=422,headers=headers())
    return asset('index.html','text/html')
@router.get(PREFIX+'/style.css')
def company_style():return asset('style.css','text/css')
@router.get(PREFIX+'/flow.mjs')
def company_flow():return asset('flow.mjs','text/javascript')
@router.get(PREFIX+'/boot.mjs')
def company_boot():return asset('boot.mjs','text/javascript')
