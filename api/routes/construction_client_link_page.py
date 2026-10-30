"""Public confirmation shell; all authority remains in authenticated APIs."""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from api.routes.construction_invitation_page import HEADERS

PREFIX = "/construction/client-user-link/confirm"
ASSETS = Path(__file__).resolve().parents[2] / "ui/web/client_link_confirmation"
FILES = {
    "index.html": "text/html",
    "style.css": "text/css",
    "flow.mjs": "text/javascript",
    "boot.mjs": "text/javascript",
}
router = APIRouter(include_in_schema=False)

def serve(request, name):
    if request.query_params:
        return Response(
            "Confirmation links must use a URL fragment.",
            status_code=422, headers=HEADERS,
        )
    if name not in FILES:
        return Response(status_code=404, headers=HEADERS)
    return Response(
        content=(ASSETS / name).read_bytes(),
        media_type=FILES[name], headers=HEADERS,
    )

@router.get(PREFIX)
def client_link_confirmation_page(request: Request):
    return serve(request, "index.html")

@router.get(PREFIX + "/{name}")
def client_link_confirmation_asset(request: Request, name: str):
    if name == "index.html":
        return Response(status_code=404, headers=HEADERS)
    return serve(request, name)

# client_link_confirmation_page_v1
