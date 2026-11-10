"""Read-only organization template queries; tenant filters on every relation."""
from sqlalchemy import func, select

from database.models import (
    ConstructionMeasurementTemplate,
    ConstructionMeasurementTemplateVersion,
    ConstructionMeasurementTemplateSection,
    ConstructionMeasurementTemplateQuestion,
)


class ConstructionOrganizationTemplateRepository:
    def __init__(self, session):
        self.session = session
        self.templates = ConstructionMeasurementTemplate.__table__
        self.versions = ConstructionMeasurementTemplateVersion.__table__
        self.sections = ConstructionMeasurementTemplateSection.__table__
        self.questions = ConstructionMeasurementTemplateQuestion.__table__

    def _columns(self):
        t, v = self.templates, self.versions
        return (
            v.c.id.label("version_id"), v.c.tenant_id, v.c.template_id,
            t.c.code, t.c.active, t.c.row_version.label("template_row_version"),
            v.c.version, v.c.row_version.label("version_row_version"),
            v.c.status, v.c.source_starter_version_id,
            v.c.definition_schema_version, v.c.source_language,
            v.c.title_key, v.c.description_key, v.c.labels, v.c.content_hash,
            v.c.created_by, v.c.created_at, v.c.updated_at, v.c.published_at,
        )

    def _join(self):
        t, v = self.templates, self.versions
        return v.join(t, (t.c.id == v.c.template_id) & (t.c.tenant_id == v.c.tenant_id))

    def _visible(self, *, tenant_id, as_of):
        t, v = self.templates, self.versions
        return (
            t.c.tenant_id == tenant_id, v.c.tenant_id == tenant_id,
            v.c.status == "PUBLISHED", v.c.published_at <= as_of,
        )

    async def get_version_header(self, *, tenant_id, version_id, as_of):
        query = select(*self._columns()).select_from(self._join()).where(
            *self._visible(tenant_id=tenant_id, as_of=as_of),
            self.versions.c.id == version_id)
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else dict(row)

    async def list_latest(self, *, tenant_id, limit, as_of, after_template_id=None):
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("Organization template query must be bounded.")
        columns = self._columns()
        ranked = select(
            *columns,
            func.row_number().over(
                partition_by=self.versions.c.template_id,
                order_by=self.versions.c.version.desc(),
            ).label("template_rank"),
        ).select_from(self._join()).where(
            *self._visible(tenant_id=tenant_id, as_of=as_of)
        ).subquery()
        query = select(*(ranked.c[column.key] for column in columns)).where(
            ranked.c.template_rank == 1)
        if after_template_id is not None:
            query = query.where(ranked.c.template_id > after_template_id)
        rows = (await self.session.execute(
            query.order_by(ranked.c.template_id).limit(limit)
        )).mappings().all()
        return tuple(dict(row) for row in rows)

    async def get_content(self, *, tenant_id, version_id):
        result = {}
        for name, table, maximum, ordering in (
            ("sections", self.sections, 101,
             (self.sections.c.sort_order, self.sections.c.id)),
            ("questions", self.questions, 2001,
             (self.questions.c.template_section_id,
              self.questions.c.sort_order, self.questions.c.id)),
        ):
            rows = (await self.session.execute(
                select(table).where(
                    table.c.tenant_id == tenant_id,
                    table.c.template_version_id == version_id,
                ).order_by(*ordering).limit(maximum)
            )).mappings().all()
            result[name] = tuple(dict(row) for row in rows)
        return result
