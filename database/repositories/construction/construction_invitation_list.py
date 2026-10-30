from sqlalchemy import and_, or_, select
from database.models import ConstructionInvitation, ConstructionInvitationScope

class ConstructionInvitationListRepository:
    def __init__(self, session):
        self.session = session

    async def list_rows(self, *, tenant_id, project_id=None):
        invitation = ConstructionInvitation
        scope = ConstructionInvitationScope
        filters = [invitation.tenant_id == tenant_id]
        if project_id is not None:
            target = ConstructionInvitationScope.__table__.alias("invitation_target")
            filters.append(
                select(target.c.id).where(
                    target.c.tenant_id == invitation.tenant_id,
                    target.c.invitation_id == invitation.id,
                    or_(
                        and_(target.c.scope_type == "project", target.c.scope_id == project_id),
                        and_(target.c.scope_type == "tenant", target.c.scope_id == tenant_id),
                    ),
                ).correlate(invitation).exists()
            )
        statement = (
            select(
                invitation.id, invitation.tenant_id, invitation.email, invitation.role,
                invitation.status, invitation.expires_at, invitation.grant_expires_at,
                invitation.row_version, scope.scope_type, scope.scope_id,
            )
            .outerjoin(scope, and_(scope.tenant_id == invitation.tenant_id, scope.invitation_id == invitation.id))
            .where(*filters)
            .order_by(invitation.created_at.desc(), invitation.id.desc(), scope.scope_type, scope.scope_id)
        )
        result = await self.session.execute(statement)
        return list(result.mappings().all())
