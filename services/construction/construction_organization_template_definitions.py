"""Organization definition v1: shared question rules, both work-type sources."""
from dataclasses import dataclass
import json
from typing import Literal

from pydantic import Field

from services.construction.construction_starter_template_definitions import (
    Question, Section, Definition, StarterTemplateDefinitionError,
    _validate_definition,
)


class OrganizationTemplateDefinitionError(ValueError):
    pass


class OrganizationQuestion(Question):
    work_type_source: Literal["MASTER", "ORGANIZATION"] | None = None


class OrganizationSection(Section):
    questions: tuple[OrganizationQuestion, ...] = Field(min_length=1, max_length=2000)


class OrganizationDefinition(Definition):
    sections: tuple[OrganizationSection, ...] = Field(min_length=1, max_length=100)


@dataclass(frozen=True)
class ValidatedOrganizationTemplateDefinition:
    source_language: str
    canonical_json: str
    content_hash: str

    def payload(self):
        return json.loads(self.canonical_json)


def validate_organization_template_definition(value, *, source_language):
    try:
        checked = _validate_definition(
            value, source_language=source_language,
            definition_model=OrganizationDefinition)
    except StarterTemplateDefinitionError:
        raise OrganizationTemplateDefinitionError(
            "Organization template definition is invalid.") from None
    return ValidatedOrganizationTemplateDefinition(
        checked.source_language, checked.canonical_json, checked.content_hash)
