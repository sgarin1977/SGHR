from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ConstructionAccessGrant,
    ConstructionAreaElement,
    ConstructionClient,
    ConstructionProject,
    ConstructionProjectArea,
    Tenant,
    User,
    UserRoleMapping,
)


class ConstructionOrganizationRepository:
    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def get_active_organization(
        self,
        *,
        tenant_id: UUID,
    ) -> Tenant | None:
        statement = (
            select(Tenant)
            .where(
                Tenant.id == tenant_id,
                Tenant.status == "active",
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()


class ConstructionAccessRepository:
    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def has_active_platform_membership(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> bool:
        secondary_membership = (
            select(UserRoleMapping.id)
            .where(
                UserRoleMapping.user_id
                == User.id,
                UserRoleMapping.tenant_id
                == tenant_id,
                UserRoleMapping.status
                == "active",
            )
            .exists()
        )

        statement = (
            select(User.id)
            .where(
                User.id == user_id,
                User.status == "active",
                or_(
                    User.tenant_id == tenant_id,
                    secondary_membership,
                ),
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return (
            result.scalar_one_or_none()
            is not None
        )

    async def get_grant_for_update(
        self,
        *,
        tenant_id: UUID,
        grant_id: UUID,
    ) -> ConstructionAccessGrant | None:
        statement = (
            select(ConstructionAccessGrant)
            .where(
                ConstructionAccessGrant.tenant_id
                == tenant_id,
                ConstructionAccessGrant.id
                == grant_id,
            )
            .limit(1)
            .execution_options(
                populate_existing=True,
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def create_grant(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        role: str,
        scope_type: str,
        scope_id: UUID,
        expires_at: datetime | None,
        granted_by_user_id: UUID,
        granted_at: datetime,
    ) -> ConstructionAccessGrant:
        grant = ConstructionAccessGrant(
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            scope_type=scope_type,
            scope_id=scope_id,
            status="active",
            expires_at=expires_at,
            granted_by_user_id=(
                granted_by_user_id
            ),
            granted_at=granted_at,
            revoked_by_user_id=None,
            revoked_at=None,
        )

        self.session.add(grant)
        await self.session.flush()
        return grant

    async def revoke_grant(
        self,
        *,
        grant: ConstructionAccessGrant,
        revoked_by_user_id: UUID,
        revoked_at: datetime,
    ) -> ConstructionAccessGrant:
        if grant.status == "revoked":
            return grant

        if grant.status != "active":
            raise ValueError(
                "Unsupported Construction "
                "access grant status."
            )

        grant.status = "revoked"
        grant.revoked_by_user_id = (
            revoked_by_user_id
        )
        grant.revoked_at = revoked_at

        await self.session.flush()
        return grant

    async def list_active_grants(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        as_of: datetime,
        scope_type: str | None = None,
        scope_id: UUID | None = None,
    ) -> list[ConstructionAccessGrant]:
        if (
            (scope_type is None)
            != (scope_id is None)
        ):
            raise ValueError(
                "Construction grant scope filter "
                "must be complete."
            )

        filters = [
            ConstructionAccessGrant.tenant_id
            == tenant_id,
            ConstructionAccessGrant.user_id
            == user_id,
            ConstructionAccessGrant.status
            == "active",
            or_(
                ConstructionAccessGrant.expires_at
                .is_(None),
                ConstructionAccessGrant.expires_at
                > as_of,
            ),
        ]

        if scope_type is not None:
            filters.extend(
                (
                    ConstructionAccessGrant.scope_type
                    == scope_type,
                    ConstructionAccessGrant.scope_id
                    == scope_id,
                )
            )

        statement = (
            select(ConstructionAccessGrant)
            .where(*filters)
            .order_by(
                ConstructionAccessGrant.role,
                ConstructionAccessGrant.scope_type,
                ConstructionAccessGrant.scope_id,
            )
        )

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())


class ConstructionClientRepository:
    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def create_client(
        self,
        *,
        tenant_id: UUID,
        display_name: str,
        client_type: str,
        phone: str | None,
        email: str | None,
        notes: str | None,
    ) -> ConstructionClient:
        client = ConstructionClient(
            tenant_id=tenant_id,
            display_name=display_name,
            client_type=client_type,
            phone=phone,
            email=email,
            notes=notes,
            deleted_at=None,
        )

        self.session.add(client)
        await self.session.flush()
        return client

    async def get_active_client(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> ConstructionClient | None:
        statement = (
            select(ConstructionClient)
            .where(
                ConstructionClient.tenant_id
                == tenant_id,
                ConstructionClient.id
                == client_id,
                ConstructionClient.deleted_at
                .is_(None),
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def get_active_client_for_update(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID,
    ) -> ConstructionClient | None:
        statement = (
            select(ConstructionClient)
            .where(
                ConstructionClient.tenant_id
                == tenant_id,
                ConstructionClient.id
                == client_id,
                ConstructionClient.deleted_at
                .is_(None),
            )
            .limit(1)
            .execution_options(
                populate_existing=True,
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def update_client(
        self,
        *,
        client: ConstructionClient,
        display_name: str,
        client_type: str,
        phone: str | None,
        email: str | None,
        notes: str | None,
    ) -> ConstructionClient:
        client.display_name = display_name
        client.client_type = client_type
        client.phone = phone
        client.email = email
        client.notes = notes

        await self.session.flush()
        return client

    async def soft_delete_client(
        self,
        *,
        client: ConstructionClient,
        deleted_at: datetime,
    ) -> ConstructionClient:
        client.deleted_at = deleted_at
        await self.session.flush()
        return client


class ConstructionProjectRepository:
    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def create_project(
        self,
        *,
        tenant_id: UUID,
        client_id: UUID | None,
        name: str,
        responsible_user_id: UUID | None,
        comment: str | None,
        address_raw: str,
        created_by: UUID,
    ) -> ConstructionProject:
        project = ConstructionProject(
            tenant_id=tenant_id,
            client_id=client_id,
            name=name,
            status="draft",
            responsible_user_id=(
                responsible_user_id
            ),
            comment=comment,
            address_raw=address_raw,
            address_formatted=None,
            country_code=None,
            region=None,
            city=None,
            postal_code=None,
            latitude=None,
            longitude=None,
            address_provider=None,
            provider_place_id=None,
            address_verification_status=(
                "pending"
            ),
            created_by=created_by,
            deleted_at=None,
            row_version=1,
        )

        self.session.add(project)
        await self.session.flush()
        return project

    async def get_active_project(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
    ) -> ConstructionProject | None:
        statement = (
            select(ConstructionProject)
            .where(
                ConstructionProject.tenant_id
                == tenant_id,
                ConstructionProject.id
                == project_id,
                ConstructionProject.deleted_at
                .is_(None),
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def get_active_project_for_update(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
    ) -> ConstructionProject | None:
        statement = (
            select(ConstructionProject)
            .where(
                ConstructionProject.tenant_id
                == tenant_id,
                ConstructionProject.id
                == project_id,
                ConstructionProject.deleted_at
                .is_(None),
            )
            .limit(1)
            .execution_options(
                populate_existing=True,
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def update_project(
        self,
        *,
        project: ConstructionProject,
        name: str,
        client_id: UUID | None,
        responsible_user_id: UUID | None,
        comment: str | None,
        address_raw: str,
        reset_address_verification: bool,
    ) -> ConstructionProject:
        project.name = name
        project.client_id = client_id
        project.responsible_user_id = (
            responsible_user_id
        )
        project.comment = comment
        project.address_raw = address_raw

        if reset_address_verification:
            project.address_formatted = None
            project.country_code = None
            project.region = None
            project.city = None
            project.postal_code = None
            project.latitude = None
            project.longitude = None
            project.address_provider = None
            project.provider_place_id = None
            project.address_verification_status = (
                "pending"
            )

        await self.session.flush()
        return project

    async def set_project_status(
        self,
        *,
        project: ConstructionProject,
        status: str,
    ) -> ConstructionProject:
        project.status = status
        await self.session.flush()
        return project


class ConstructionProjectAreaRepository:
    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def create_area(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        area_type: str,
        name: str,
        sort_order: int | None,
        created_by: UUID,
    ) -> ConstructionProjectArea:
        area = ConstructionProjectArea(
            tenant_id=tenant_id,
            project_id=project_id,
            area_type=area_type,
            name=name,
            sort_order=sort_order,
            status="draft",
            created_by=created_by,
            deleted_at=None,
            row_version=1,
        )

        self.session.add(area)
        await self.session.flush()
        return area

    async def get_active_area_for_update(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        area_id: UUID,
    ) -> ConstructionProjectArea | None:
        statement = (
            select(ConstructionProjectArea)
            .where(
                ConstructionProjectArea.tenant_id
                == tenant_id,
                ConstructionProjectArea.project_id
                == project_id,
                ConstructionProjectArea.id
                == area_id,
                ConstructionProjectArea.deleted_at
                .is_(None),
            )
            .limit(1)
            .execution_options(
                populate_existing=True,
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def set_area_status(
        self,
        *,
        area: ConstructionProjectArea,
        status: str,
    ) -> ConstructionProjectArea:
        area.status = status
        await self.session.flush()
        return area

    async def rename_area(
        self,
        *,
        area: ConstructionProjectArea,
        name: str,
    ) -> ConstructionProjectArea:
        area.name = name
        await self.session.flush()
        return area

    async def has_active_elements(
        self,
        *,
        tenant_id: UUID,
        project_area_id: UUID,
    ) -> bool:
        statement = (
            select(ConstructionAreaElement.id)
            .where(
                ConstructionAreaElement.tenant_id
                == tenant_id,
                ConstructionAreaElement.project_area_id
                == project_area_id,
                ConstructionAreaElement.deleted_at
                .is_(None),
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return (
            result.scalar_one_or_none()
            is not None
        )

    async def soft_delete_area(
        self,
        *,
        area: ConstructionProjectArea,
        deleted_at: datetime,
    ) -> ConstructionProjectArea:
        area.deleted_at = deleted_at
        await self.session.flush()
        return area

    async def get_active_area(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        area_id: UUID,
    ) -> ConstructionProjectArea | None:
        statement = (
            select(ConstructionProjectArea)
            .where(
                ConstructionProjectArea.tenant_id
                == tenant_id,
                ConstructionProjectArea.project_id
                == project_id,
                ConstructionProjectArea.id
                == area_id,
                ConstructionProjectArea.deleted_at
                .is_(None),
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def list_active_areas(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
    ) -> list[ConstructionProjectArea]:
        statement = (
            select(ConstructionProjectArea)
            .where(
                ConstructionProjectArea.tenant_id
                == tenant_id,
                ConstructionProjectArea.project_id
                == project_id,
                ConstructionProjectArea.deleted_at
                .is_(None),
            )
            .order_by(
                ConstructionProjectArea.sort_order
                .asc()
                .nulls_last(),
                ConstructionProjectArea.created_at
                .asc(),
                ConstructionProjectArea.id.asc(),
            )
        )

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())

    async def set_area_sort_order(
        self,
        *,
        area: ConstructionProjectArea,
        sort_order: int | None,
    ) -> ConstructionProjectArea:
        area.sort_order = sort_order
        await self.session.flush()
        return area


class ConstructionAreaElementRepository:
    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def create_element(
        self,
        *,
        tenant_id: UUID,
        project_area_id: UUID,
        parent_element_id: UUID | None,
        element_type: str,
        name: str,
        sort_order: int | None,
        geometry_json: dict,
        created_by: UUID,
    ) -> ConstructionAreaElement:
        element = ConstructionAreaElement(
            tenant_id=tenant_id,
            project_area_id=project_area_id,
            parent_element_id=parent_element_id,
            element_type=element_type,
            name=name,
            sort_order=sort_order,
            geometry_json=geometry_json,
            created_by=created_by,
            deleted_at=None,
            row_version=1,
        )

        self.session.add(element)
        await self.session.flush()
        return element

    async def get_active_parent_wall(
        self,
        *,
        tenant_id: UUID,
        project_area_id: UUID,
        parent_element_id: UUID,
    ) -> ConstructionAreaElement | None:
        statement = (
            select(ConstructionAreaElement)
            .where(
                ConstructionAreaElement.tenant_id
                == tenant_id,
                ConstructionAreaElement.project_area_id
                == project_area_id,
                ConstructionAreaElement.id
                == parent_element_id,
                ConstructionAreaElement.element_type
                == "WALL",
                ConstructionAreaElement.deleted_at
                .is_(None),
            )
            .limit(1)
            .execution_options(
                populate_existing=True,
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def list_active_elements(
        self,
        *,
        tenant_id: UUID,
        project_area_id: UUID,
    ) -> list[ConstructionAreaElement]:
        statement = (
            select(ConstructionAreaElement)
            .where(
                ConstructionAreaElement.tenant_id
                == tenant_id,
                ConstructionAreaElement.project_area_id
                == project_area_id,
                ConstructionAreaElement.deleted_at
                .is_(None),
            )
            .order_by(
                ConstructionAreaElement.sort_order
                .asc()
                .nulls_last(),
                ConstructionAreaElement.created_at
                .asc(),
                ConstructionAreaElement.id.asc(),
            )
        )

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())

    async def get_active_element(
        self,
        *,
        tenant_id: UUID,
        project_area_id: UUID,
        element_id: UUID,
    ) -> ConstructionAreaElement | None:
        statement = (
            select(ConstructionAreaElement)
            .where(
                ConstructionAreaElement.tenant_id
                == tenant_id,
                ConstructionAreaElement.project_area_id
                == project_area_id,
                ConstructionAreaElement.id
                == element_id,
                ConstructionAreaElement.deleted_at
                .is_(None),
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def get_active_element_for_update(
        self,
        *,
        tenant_id: UUID,
        project_area_id: UUID,
        element_id: UUID,
    ) -> ConstructionAreaElement | None:
        statement = (
            select(ConstructionAreaElement)
            .where(
                ConstructionAreaElement.tenant_id
                == tenant_id,
                ConstructionAreaElement.project_area_id
                == project_area_id,
                ConstructionAreaElement.id
                == element_id,
                ConstructionAreaElement.deleted_at
                .is_(None),
            )
            .limit(1)
            .execution_options(
                populate_existing=True,
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def update_element(
        self,
        *,
        element: ConstructionAreaElement,
        name: str,
        sort_order: int | None,
        geometry_json: dict,
        parent_element_id: UUID | None,
    ) -> ConstructionAreaElement:
        element.name = name
        element.sort_order = sort_order
        element.geometry_json = geometry_json
        element.parent_element_id = (
            parent_element_id
        )

        await self.session.flush()
        return element

    async def has_active_wall_children(
        self,
        *,
        tenant_id: UUID,
        project_area_id: UUID,
        wall_element_id: UUID,
    ) -> bool:
        statement = (
            select(ConstructionAreaElement.id)
            .where(
                ConstructionAreaElement.tenant_id
                == tenant_id,
                ConstructionAreaElement.project_area_id
                == project_area_id,
                ConstructionAreaElement.parent_element_id
                == wall_element_id,
                ConstructionAreaElement.element_type
                .in_(
                    (
                        "WINDOW",
                        "DOOR",
                        "OPENING",
                    )
                ),
                ConstructionAreaElement.deleted_at
                .is_(None),
            )
            .limit(1)
        )

        result = await self.session.execute(
            statement
        )
        return (
            result.scalar_one_or_none()
            is not None
        )

    async def soft_delete_element(
        self,
        *,
        element: ConstructionAreaElement,
        deleted_at: datetime,
    ) -> ConstructionAreaElement:
        element.deleted_at = deleted_at
        element.row_version += 1
        await self.session.flush()
        return element
