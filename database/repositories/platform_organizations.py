"""Organization discovery from canonical Platform membership."""
from uuid import UUID

from sqlalchemy import or_, select

from database.models import Tenant, User, UserRoleMapping


class PlatformOrganizationsRepository:
    def __init__(self, session):
        self.session = session

    async def list_organizations(
        self, *, user_id, after=None, limit=10
    ):
        if (
            not isinstance(user_id, UUID)
            or (
                after is not None
                and not isinstance(after, UUID)
            )
            or type(limit) is not int
            or not 1 <= limit <= 20
        ):
            raise ValueError("Invalid organization page.")

        secondary = (
            select(UserRoleMapping.id)
            .where(
                UserRoleMapping.user_id == User.id,
                UserRoleMapping.tenant_id == Tenant.id,
                UserRoleMapping.status == "active",
            )
            .correlate(User, Tenant)
            .exists()
        )
        member = (
            select(User.id)
            .where(
                User.id == user_id,
                User.status == "active",
                or_(
                    User.tenant_id == Tenant.id,
                    secondary,
                ),
            )
            .correlate(Tenant)
            .exists()
        )
        query = select(Tenant.id, Tenant.name).where(
            Tenant.status == "active",
            member,
        )
        if after is not None:
            query = query.where(Tenant.id > after)

        result = await self.session.execute(
            query.order_by(Tenant.id).limit(limit + 1)
        )
        return [
            dict(row)
            for row in result.mappings().all()
        ]
