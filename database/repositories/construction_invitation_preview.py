from types import SimpleNamespace
from sqlalchemy import and_, select
from database.models import ConstructionInvitation, ConstructionInvitationScope

class ConstructionInvitationPreviewRepository:

    def __init__(self, session):
        self.session = session

    async def get_snapshot(self, *, tenant_id, invitation_id):
        statement = select(ConstructionInvitation, ConstructionInvitationScope.scope_type, ConstructionInvitationScope.scope_id).outerjoin(ConstructionInvitationScope, and_(ConstructionInvitationScope.tenant_id == ConstructionInvitation.tenant_id, ConstructionInvitationScope.invitation_id == ConstructionInvitation.id)).where(ConstructionInvitation.tenant_id == tenant_id, ConstructionInvitation.id == invitation_id).order_by(ConstructionInvitationScope.scope_type, ConstructionInvitationScope.scope_id).execution_options(populate_existing=True)
        rows = (await self.session.execute(statement)).all()
        if not rows:
            return None
        return SimpleNamespace(invitation=rows[0][0], scopes=tuple(((kind, target) for _, kind, target in rows)))
