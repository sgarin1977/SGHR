"""CON-C016 structural contract, reserved for the future Measurement domain.

Sources: rev.12 sections 4.1, 22 and Stage 1A CON-C016; the CON-C016
clarification explicitly defers storage; the later Measurement/Area boundary
clarification also defers endpoint implementation and acceptance.

JSON field names below are technical projection names for the documented
number/project/date/state. This module is neither a repository nor a router.
Importing it must not allocate numbers, create surrogate records, query a
database, or publish an endpoint returning fabricated/empty measurement data.
"""
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr, field_validator


MEASUREMENT_FEED_PATH = '/api/v1/construction/measurements'
MEASUREMENT_FEED_READ_PERMISSION = 'construction.measurements.read'
MEASUREMENT_FEED_IMPLEMENTATION_STAGE = 'Measurement'

# Positive sequential numbers are displayed with at least four digits. This
# validates an already assigned representation; it does not own a sequence.
MeasurementFeedNumber = Annotated[StrictStr, Field(
    pattern=r'^(?:0{3}[1-9]|0{2}[1-9][0-9]|0[1-9][0-9]{2}|[1-9][0-9]{3,})$',
    description='Already assigned measurement number, formatted with at least four digits.',
)]
MeasurementFeedStatus = Annotated[StrictStr, Field(
    pattern=r'^[a-z][a-z0-9_]*$',
    description=('Canonical Measurement state code; the future domain validates '
                 'the allowed states. This contract declares no new state enum.'),
)]


class MeasurementFeedItem(BaseModel):
    """Minimum documented feed projection, independent of physical storage."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    id: UUID
    project_id: UUID
    number: MeasurementFeedNumber
    project_name: Annotated[StrictStr, Field(min_length=1)]
    status: MeasurementFeedStatus
    created_at: AwareDatetime = Field(
        description='Server-assigned creation instant, set when the measurement actually starts.',
    )

    @field_validator('project_name')
    @classmethod
    def nonblank_project_name(cls, value):
        if not value.strip():
            raise ValueError('Project name must not be blank.')
        return value


class MeasurementFeedEnvelope(BaseModel):
    """Use the existing Platform data/meta/request_id envelope."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    data: list[MeasurementFeedItem]
    meta: dict
    request_id: Annotated[StrictStr, Field(min_length=1)]


def measurement_feed_openapi_contract():
    """Return an isolated OpenAPI document, never mounted in the current app.

    Only the approved mine=true entrypoint is described. Pagination, ordering,
    search/filter details, the canonical lifecycle enum and the exact domain
    relation for 'mine' are not inferred from CRM or the client-card contracts.
    They belong to the full Measurement implementation. A 'mine' filter never
    grants access or replaces fresh tenant/project-scope authorization.
    """
    schema = MeasurementFeedEnvelope.model_json_schema(
        mode='serialization', ref_template='#/components/schemas/{model}',
    )
    components = schema.pop('$defs', {})
    components['MeasurementFeedEnvelope'] = schema
    return {
        'openapi': '3.1.0',
        'info': {'title': 'CON-C016 Measurement feed — deferred contract', 'version': '1.0'},
        'x-sghr-contract-only': True,
        'x-sghr-runtime-registered': False,
        'x-sghr-implementation-stage': MEASUREMENT_FEED_IMPLEMENTATION_STAGE,
        'x-sghr-deferred-details': [
            'Canonical Measurement lifecycle codes',
            'Domain relation selecting mine=true measurements',
            'Feed ordering, pagination and search/filter details',
        ],
        'paths': {MEASUREMENT_FEED_PATH: {'get': {
            'operationId': 'constructionMeasurementFeed',
            'summary': 'Draft and recent measurements visible to the current user',
            'description': (
                'Deferred until the full Measurement stage. Uses existing Platform '
                'authentication, trusted tenant context, active membership, '
                'Construction Suite/Core gates and fresh project-scope access. '
                'mine=true narrows authorized results and grants no additional access. '
                'Uses the shared authenticated rate limiter. No surrogate storage, '
                'number allocation or runtime handler is introduced by this contract.'
            ),
            'security': [{'BearerAuth': []}],
            'x-sghr-permission': MEASUREMENT_FEED_READ_PERMISSION,
            'x-sghr-modules': ['construction', 'construction.core'],
            'parameters': [{
                'name': 'mine', 'in': 'query', 'required': True,
                'description': 'Approved personal-feed entrypoint; not a substitute for scope checks.',
                'schema': {'type': 'boolean', 'const': True},
                'example': True,
            }],
            'responses': {
                '200': {'description': 'Authorized measurement feed', 'content': {
                    'application/json': {'schema': {'$ref': '#/components/schemas/MeasurementFeedEnvelope'}},
                }},
                '401': {'description': 'Existing Platform authentication required'},
                '403': {'description': 'Module disabled or measurement read/scope access denied'},
                '422': {'description': 'Invalid feed query'},
                '429': {'description': 'Shared authenticated rate limit exceeded'},
                '500': {'description': 'Sanitized read failure'},
            },
        }}},
        'components': {
            'schemas': components,
            'securitySchemes': {'BearerAuth': {'type': 'http', 'scheme': 'bearer', 'bearerFormat': 'JWT'}},
        },
    }
