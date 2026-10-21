from datetime import datetime, timezone

from services.construction_permissions import (
    ConstructionPermissionDeniedError,
    ConstructionRolePermissionPolicy,
    require_construction_access,
)


def resolve_team_identity(*, display_names, verified_emails):
    names = [
        name
        for name in display_names
        if isinstance(name, str) and name.strip()
    ]
    emails = {
        email
        for email in verified_emails
        if isinstance(email, str) and email.strip()
    }
    return {
        "display_name": names[0] if len(names) == 1 else None,
        "email": next(iter(emails)) if len(emails) == 1 else None,
    }


class ConstructionTeamService:
    def __init__(
        self, *, repository, now_provider=None, identity_reader=None,
    ):
        self.repository = repository
        self.identity_reader = identity_reader
        self.now_provider = (
            now_provider
            or (lambda: datetime.now(timezone.utc))
        )

    async def list_team_grants(
        self,
        *,
        actor,
        access_context,
        project_id=None,
    ):
        if actor.status != "active":
            raise ConstructionPermissionDeniedError(
                "Construction team is not available."
            )

        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code="construction.team.manage",
            project_id=project_id,
        )
        return await self.repository.list_active_grants(
            tenant_id=actor.tenant_id,
            as_of=self.now_provider(),
            project_id=project_id,
        )
    async def list_team_members(
        self, *, actor, access_context, project_id=None,
    ):
        grants = await self.list_team_grants(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
        )
        policy = ConstructionRolePermissionPolicy()
        members = {}
        for grant in grants:
            permissions = await policy.list_permissions(
                roles=(grant.role,),
            )
            member = members.setdefault(
                grant.user_id,
                {
                    "user_id": grant.user_id,
                    "roles": [],
                    "grants": [],
                    "scoped_permissions": [],
                },
            )
            if grant.role not in member["roles"]:
                member["roles"].append(grant.role)
            member["grants"].append(grant)
            member["scoped_permissions"].append({
                "grant_id": grant.id,
                "role": grant.role,
                "scope_type": grant.scope_type,
                "scope_id": grant.scope_id,
                "permissions": list(permissions),
            })

        for member in members.values():
            member["roles"].sort()
            member["display_name"] = None
            member["email"] = None

        if self.identity_reader is not None and members:
            rows = await self.identity_reader.list_team_identity_accounts(
                tenant_id=actor.tenant_id,
                user_ids=tuple(members),
            )
            identities = {
                user_id: {"display_names": [], "verified_emails": []}
                for user_id in members
            }
            for row in rows:
                user_id = row["user_id"]
                if user_id not in identities:
                    raise ConstructionPermissionDeniedError(
                        "Construction team identity is not available."
                    )
                identity = identities[user_id]
                identity["display_names"].append(row.get("display_name"))
                identity["verified_emails"].append(row.get("verified_email"))

            for user_id, identity in identities.items():
                members[user_id].update(resolve_team_identity(**identity))

        return list(members.values())



def build_construction_team_service(
    *, construction_session, platform_session,
):
    from database.repositories.construction_team import (
        ConstructionTeamRepository,
    )
    from database.repositories.user import UserRepository

    if (
        construction_session is None
        or platform_session is None
        or construction_session is platform_session
    ):
        raise ValueError(
            "Construction and Platform sessions must be separate."
        )

    return ConstructionTeamService(
        repository=ConstructionTeamRepository(construction_session),
        identity_reader=UserRepository(platform_session),
    )


def build_construction_team_access(session):
    from database.repositories.construction import (
        ConstructionAccessRepository,
    )
    from database.repositories.construction_team import (
        ConstructionTeamRepository,
    )
    from services.construction_grants import (
        ConstructionAccessContextResolver,
    )

    return (
        ConstructionTeamService(
            repository=ConstructionTeamRepository(session),
        ),
        ConstructionAccessContextResolver(
            repository=ConstructionAccessRepository(session),
        ),
    )
