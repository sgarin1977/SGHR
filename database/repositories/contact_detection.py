from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ContactDetectionEvent,
    Message,
    RiskFlag,
    ThreadRestriction,
)


class ContactDetectionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_message(self, message_id: UUID) -> Message | None:
        result = await self.session.execute(
            select(Message).where(Message.id == message_id)
        )
        return result.scalar_one_or_none()

    async def mark_message_masked(
        self,
        *,
        message: Message,
        masked_text: str,
        detected_types: list[str],
        action_taken: str,
    ) -> Message:
        metadata = dict(message.extra_metadata or {})
        metadata["contact_detection"] = {
            "detected_types": detected_types,
            "action_taken": action_taken,
            "masked_at": datetime.utcnow().isoformat(),
        }

        message.original_text = masked_text
        message.is_masked = True
        message.extra_metadata = metadata
        await self.session.flush()
        return message

    async def log_detection(
        self,
        *,
        tenant_id: UUID,
        message_id: UUID,
        detected_type: str,
        confidence: float,
        action_taken: str,
    ) -> ContactDetectionEvent:
        event = ContactDetectionEvent(
            tenant_id=tenant_id,
            message_id=message_id,
            detected_type=detected_type,
            confidence=confidence,
            action_taken=action_taken,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def create_risk_flag(
        self,
        *,
        tenant_id: UUID,
        entity_type: str,
        entity_id: UUID,
        flag_code: str,
        severity: str,
        details: dict,
    ) -> RiskFlag:
        flag = RiskFlag(
            tenant_id=tenant_id,
            entity_type=entity_type,
            entity_id=entity_id,
            flag_code=flag_code,
            severity=severity,
            status="open",
            details=details,
        )
        self.session.add(flag)
        await self.session.flush()
        return flag

    async def restrict_thread(
        self,
        *,
        thread_id: UUID,
        reason: str,
        expires_in_hours: int = 24,
    ) -> ThreadRestriction:
        restriction = ThreadRestriction(
            thread_id=thread_id,
            reason=reason,
            status="active",
            expires_at=datetime.utcnow() + timedelta(hours=expires_in_hours),
        )
        self.session.add(restriction)
        await self.session.flush()
        return restriction