"""Own identity-link routing projection, under read-only caller-bound RLS.

This is not a client-card repository. The result carries no client details,
row version, project data, membership or permissions. Missing, archived and
foreign-owned clients all produce None. No write or lock is performed here.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from database.models import ConstructionClient
from database.construction_client_unlink_context import OWN_LINK_CLIENT_SETTING, OWN_LINK_USER_SETTING


@dataclass(frozen=True)
class ConstructionClientOwnLinkContext:
    client_id: UUID
    tenant_id: UUID
    user_id: UUID

    def __post_init__(self):
        if not all(isinstance(value,UUID) for value in (self.client_id,self.tenant_id,self.user_id)):
            raise ValueError('Own-link context requires canonical UUID identifiers.')


class ConstructionClientOwnLinkContextRepository:
    def __init__(self, session):
        if (session is None or not callable(getattr(session,'execute',None))
                or not callable(getattr(session,'in_transaction',None)) or not hasattr(session,'no_autoflush')):
            raise ValueError('Own-link context reads require a transaction-capable Construction session.')
        self.session=session

    async def find_own_link(self, *, user_id, client_id):
        if not isinstance(user_id,UUID) or not isinstance(client_id,UUID):
            raise ValueError('Own-link lookup requires canonical UUID identifiers.')
        if self.session.in_transaction() is not True:
            raise ValueError('Own-link lookup requires an existing caller transaction.')
        c=ConstructionClient.__table__.c
        # Runtime helper verifies role/RLS. These predicates additionally bind
        # this exact projection to the read-only, tenantless ownership context.
        statement=select(c.id.label('client_id'),c.tenant_id,c.linked_user_id.label('user_id')).where(
            c.id==client_id, c.linked_user_id==user_id, c.deleted_at.is_(None),
            func.current_setting('transaction_read_only')=='on',
            func.nullif(func.current_setting('app.current_tenant_id',True),'').is_(None),
            func.nullif(func.current_setting('app.current_invitation_id',True),'').is_(None),
            func.nullif(func.current_setting('app.current_invitation_recipient_id',True),'').is_(None),
            func.current_setting(OWN_LINK_USER_SETTING,True)==str(user_id),
            func.current_setting(OWN_LINK_CLIENT_SETTING,True)==str(client_id),
        ).execution_options(autoflush=False)
        with self.session.no_autoflush:
            row=(await self.session.execute(statement)).mappings().one_or_none()
        if self.session.in_transaction() is not True:
            raise ValueError('Own-link lookup transaction ended unexpectedly.')
        if row is None:return None
        if (not isinstance(row,Mapping) or set(row)!={'client_id','tenant_id','user_id'}
                or row['client_id']!=client_id or row['user_id']!=user_id):
            raise ValueError('Own-link lookup receipt has invalid ownership context.')
        return ConstructionClientOwnLinkContext(**row)
