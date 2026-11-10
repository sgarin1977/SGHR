"""Read-only global starter catalog; SQL and native RLS both restrict visibility."""
from sqlalchemy import select, func
from database.models import (
    ConstructionStarterMeasurementTemplate,
    ConstructionStarterMeasurementTemplateVersion,
)


class ConstructionStarterTemplateRepository:
    def __init__(self, session):
        self.session = session
        self.templates = ConstructionStarterMeasurementTemplate.__table__
        self.versions = ConstructionStarterMeasurementTemplateVersion.__table__

    def _columns(self):
        t, v = self.templates, self.versions
        return (
            v.c.id.label("version_id"), v.c.template_id, t.c.code, t.c.active,
            v.c.version, v.c.definition_schema_version, v.c.source_language,
            v.c.definition, v.c.content_hash, v.c.published_at, v.c.created_at,
        )

    async def get_version(self, *, version_id, as_of):
        t, v = self.templates, self.versions
        query = select(*self._columns()).select_from(
            v.join(t, t.c.id == v.c.template_id)
        ).where(
            v.c.id == version_id, t.c.active.is_(True),
            v.c.published_at <= as_of,
        )
        row = (await self.session.execute(query)).mappings().one_or_none()
        return None if row is None else dict(row)

    async def list_latest(self, *, limit, as_of, after_template_id=None):
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("Catalog query must be bounded.")
        t, v = self.templates, self.versions
        columns = self._columns()
        ranked = select(
            *columns,
            func.row_number().over(
                partition_by=v.c.template_id, order_by=v.c.version.desc()
            ).label("catalog_rank"),
        ).select_from(v.join(t, t.c.id == v.c.template_id)).where(
            t.c.active.is_(True), v.c.published_at <= as_of,
        ).subquery()
        query = select(*(ranked.c[column.key] for column in columns)).where(
            ranked.c.catalog_rank == 1)
        if after_template_id is not None:
            query = query.where(ranked.c.template_id > after_template_id)
        rows = (await self.session.execute(
            query.order_by(ranked.c.template_id).limit(limit)
        )).mappings().all()
        return tuple(dict(row) for row in rows)
