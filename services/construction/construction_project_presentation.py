"""Profile-language project presentation through the existing project GET."""
from dataclasses import dataclass
import re
from uuid import UUID

from api.routes.construction_project_read import ProjectDetailsResponse
from api.construction_project_localization import LocalizedProjectMeta, PlainProjectMeta
from services.construction.construction_api_client import (
    ConstructionApiResult, ConstructionApiClientError,
)


@dataclass(frozen=True)
class ProjectPresentationResult(ConstructionApiResult):
    translation_unavailable: bool = False


def invalid():
    return ConstructionApiClientError(status_code=502, code="invalid_api_response")


async def read_localized_project(client, *, session, project_id, expected_language):
    if (
        not isinstance(project_id, UUID) or not isinstance(expected_language, str)
        or len(expected_language) > 10
        or re.fullmatch(r"[a-z]{2,3}([-_][A-Za-z0-9]{2,6})?", expected_language) is None
    ):
        raise ValueError("An exact project and current profile language are required.")
    try:
        result = await client._get(
            "construction/projects/" + str(project_id), session=session,
            params={"include_translations": "true"},
        )
    except ConstructionApiClientError as error:
        if error.status_code != 503 or error.code != "TRANSLATION_UNAVAILABLE":
            raise
        # Ordinary API authorization still applies to this second read.
        original = await client.project_details(session=session, project_id=project_id)
        try:
            meta = PlainProjectMeta.model_validate(original.meta)
        except (ValueError, TypeError, AttributeError):
            raise invalid() from None
        return ProjectPresentationResult(
            original.data, meta.model_dump(mode="json"), original.request_id, True,
        )

    try:
        item = ProjectDetailsResponse.model_validate(result.data)
        meta = LocalizedProjectMeta.model_validate(result.meta)
        texts = meta.translations
        if (
            item.id != project_id or item.tenant_id != session.tenant_id
            or texts.source_version != item.row_version
            or texts.name.original_text != item.name
            or texts.comment.original_text != item.comment
        ):
            raise ValueError("Project presentation context differs.")
    except (ValueError, TypeError, AttributeError):
        raise invalid() from None
    if texts.target_language != expected_language:
        raise ConstructionApiClientError(
            status_code=409, code="LANGUAGE_CONTEXT_CHANGED",
        )
    return ProjectPresentationResult(
        item.model_dump(mode="json"), meta.model_dump(mode="json"), result.request_id,
    )
