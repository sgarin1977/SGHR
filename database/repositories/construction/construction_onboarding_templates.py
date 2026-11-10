"""Canonical onboarding candidate; no template creation or progress writes."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from database.models import (
    ConstructionMeasurementTemplate,
    ConstructionMeasurementTemplateVersion,
)


class ConstructionOnboardingTemplateRepository:
    def __init__(self, session):
        if not callable(getattr(session, "execute", None)):
            raise ValueError("A shared transaction session is required.")
        self.session = session
        self.templates = ConstructionMeasurementTemplate.__table__
        self.versions = ConstructionMeasurementTemplateVersion.__table__

    async def get_active_published_candidate(self, *, tenant_id, as_of):
        if (
            not isinstance(tenant_id, UUID)
            or not isinstance(as_of, datetime)
            or as_of.tzinfo is None or as_of.utcoffset() is None
        ):
            raise ValueError("Trusted tenant and aware server time are required.")
        t, v = self.templates, self.versions
        query = (
            select(
                t.c.tenant_id,
                t.c.id.label("template_id"),
                v.c.id.label("version_id"),
            )
            .select_from(t.join(
                v,
                (v.c.template_id == t.c.id)
                & (v.c.tenant_id == t.c.tenant_id),
            ))
            .where(
                t.c.tenant_id == tenant_id,
                v.c.tenant_id == tenant_id,
                t.c.active.is_(True),
                v.c.status == "PUBLISHED",
                v.c.published_at <= as_of,
            )
            .order_by(t.c.id, v.c.version.desc())
            .limit(1)
            .with_for_update(read=True, of=(t, v))
        )
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else dict(row)
