"""Tenant-scoped template copy storage. Caller owns authorization and commit."""
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.models import (
    ConstructionMeasurementTemplate,
    ConstructionMeasurementTemplateVersion,
    ConstructionMeasurementTemplateSection,
    ConstructionMeasurementTemplateQuestion,
)

BATCH_SIZE = 100


class ConstructionTemplateCopyRepository:
    def __init__(self, session):
        self.session = session
        self.templates = ConstructionMeasurementTemplate.__table__
        self.versions = ConstructionMeasurementTemplateVersion.__table__
        self.sections = ConstructionMeasurementTemplateSection.__table__
        self.questions = ConstructionMeasurementTemplateQuestion.__table__

    def _identity_insert(self):
        return pg_insert(self.templates)

    async def create_template(self, *, tenant_id, row):
        t = self.templates
        statement = self._identity_insert().values(
            **(dict(row) | {"tenant_id": tenant_id})
        ).on_conflict_do_nothing(
            index_elements=[t.c.tenant_id, t.c.code]
        ).returning(t)
        result = (await self.session.execute(statement)).mappings().one_or_none()
        return None if result is None else dict(result)

    async def create_version(self, *, tenant_id, template_id, row):
        values = dict(row) | {
            "tenant_id": tenant_id, "template_id": template_id,
            "status": "DRAFT", "row_version": 1,
            "published_at": None, "content_hash": None,
        }
        result = (await self.session.execute(
            insert(self.versions).values(**values).returning(self.versions)
        )).mappings().one()
        return dict(result)

    async def insert_content(
        self, *, tenant_id, version_id, sections, questions,
    ):
        for table, rows in (
            (self.sections, sections), (self.questions, questions),
        ):
            for start in range(0, len(rows), BATCH_SIZE):
                values = [
                    dict(row) | {
                        "tenant_id": tenant_id,
                        "template_version_id": version_id,
                    }
                    for row in rows[start:start + BATCH_SIZE]
                ]
                await self.session.execute(insert(table).values(values))

    async def publish(
        self, *, tenant_id, template_id, version_id, content_hash, as_of,
    ):
        v = self.versions
        statement = update(v).where(
            v.c.tenant_id == tenant_id, v.c.template_id == template_id,
            v.c.id == version_id, v.c.version == 1,
            v.c.status == "DRAFT", v.c.row_version == 1,
        ).values(
            status="PUBLISHED", content_hash=content_hash,
            row_version=v.c.row_version + 1,
            published_at=as_of, updated_at=as_of,
        ).returning(v)
        row = (await self.session.execute(statement)).mappings().one_or_none()
        return None if row is None else dict(row)

    async def get_copy(self, *, tenant_id, template_id, version_id):
        result = {}
        for name, table, condition in (
            ("template", self.templates, self.templates.c.id == template_id),
            ("version", self.versions,
             (self.versions.c.id == version_id)
             & (self.versions.c.template_id == template_id)),
        ):
            row = (await self.session.execute(
                select(table).where(
                    table.c.tenant_id == tenant_id, condition)
            )).mappings().one_or_none()
            result[name] = None if row is None else dict(row)

        for name, table, maximum, order in (
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
                ).order_by(*order).limit(maximum)
            )).mappings().all()
            result[name] = tuple(dict(row) for row in rows)
        return result
