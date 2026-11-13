"""Visible-page area translations over the existing area-list API."""
from dataclasses import dataclass
import re
from uuid import UUID

from api.construction_area_localization import LocalizedAreaMeta
from api.construction_project_localization import PlainProjectMeta
from api.routes.construction_project_areas import ProjectAreaListEnvelope
from services.construction.construction_api_client import ConstructionApiClientError
from services.construction.construction_project_presentation import (
    ProjectPresentationResult, invalid,
)

PAGE_SIZE = 5


@dataclass(frozen=True)
class AreaPresentationResult(ProjectPresentationResult):
    display_offset: int = 0


def page_offset(offset, total):
    if type(offset) is not int or offset < 0:
        raise ValueError("A nonnegative page offset is required.")
    return min(offset, ((total - 1) // PAGE_SIZE) * PAGE_SIZE) if total else 0


def plain_metadata(result):
    try:
        return PlainProjectMeta.model_validate(result.meta).model_dump(mode="json")
    except (ValueError, TypeError, AttributeError):
        raise invalid() from None


async def read_localized_areas(client, *, session, project_id, expected_language, display_offset):
    if (
        not isinstance(project_id, UUID)
        or not isinstance(expected_language, str) or len(expected_language) > 10
        or re.fullmatch(r"[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?", expected_language) is None
        or type(display_offset) is not int or display_offset < 0
    ):
        raise ValueError("An exact project, profile language and page are required.")
    original = await client.project_areas(session=session, project_id=project_id)
    meta = plain_metadata(original)
    offset = page_offset(display_offset, len(original.data))
    selected = original.data[offset:offset + PAGE_SIZE]
    if not selected:
        return AreaPresentationResult(original.data, meta, original.request_id, False, offset)
    identities = [item["id"] for item in selected]
    params = [("include_translations", "true")]
    params.extend(("translation_area_ids", identity) for identity in identities)
    try:
        response = await client._get(
            "construction/projects/" + str(project_id) + "/areas",
            session=session, params=params,
        )
    except ConstructionApiClientError as error:
        if error.status_code != 503 or error.code != "TRANSLATION_UNAVAILABLE":
            raise
        fresh = await client.project_areas(session=session, project_id=project_id)
        return AreaPresentationResult(
            fresh.data, plain_metadata(fresh), fresh.request_id, True,
            page_offset(display_offset, len(fresh.data)),
        )
    try:
        envelope = ProjectAreaListEnvelope.model_validate({
            "data": response.data, "meta": response.meta, "request_id": response.request_id,
        })
        localized = LocalizedAreaMeta.model_validate(response.meta)
        rows = [item.model_dump(mode="json") for item in envelope.data]
        if (
            len({item["id"] for item in rows}) != len(rows)
            or any(
                item["tenant_id"] != str(session.tenant_id)
                or item["project_id"] != str(project_id) for item in rows
            )
        ):
            raise ValueError("Area identities differ.")
    except (ValueError, TypeError, AttributeError):
        raise invalid() from None
    if rows != original.data:
        raise ConstructionApiClientError(status_code=409, code="SOURCE_CONTEXT_CHANGED")
    texts = localized.translations
    if texts.target_language != expected_language:
        raise ConstructionApiClientError(status_code=409, code="LANGUAGE_CONTEXT_CHANGED")
    by_id = {item["id"]: item for item in selected}
    if {str(item.area_id) for item in texts.items} != set(identities):
        raise invalid()
    for item in texts.items:
        source = by_id[str(item.area_id)]
        if item.source_version != source["row_version"] or item.original_text != source["name"]:
            raise invalid()
    return AreaPresentationResult(
        rows, localized.model_dump(mode="json"), response.request_id, False, offset,
    )
