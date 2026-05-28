import re
from dataclasses import dataclass
from uuid import UUID

from database.repositories.contact_detection import ContactDetectionRepository


@dataclass(frozen=True)
class ContactDetectionMatch:
    detected_type: str
    value: str
    confidence: float


@dataclass(frozen=True)
class ContactDetectionResult:
    message_id: UUID | None
    is_masked: bool
    detected_types: list[str]
    action_taken: str | None
    thread_restricted: bool = False


class ContactDetectionService:
    EMAIL_RE = re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    )
    TELEGRAM_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{5,32}\b")
    PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
    MESSENGER_RE = re.compile(
        r"\b(?:whatsapp|telegram|viber|ватсап|вотсап|телеграм|вайбер)\b.{0,30}?(?:\+?\d[\d\s().-]{5,}\d)",
        re.IGNORECASE,
    )
    EXTERNAL_PAYMENT_RE = re.compile(
        r"\b(?:paypal|wise|revolut|iban|swift|crypto|bitcoin|btc|usdt|bank\s+transfer|"
        r"карта|карту|перевод|переказ|оплата\s+напрямую|оплата\s+напряму|pix|mb\s*way)\b",
        re.IGNORECASE,
    )

    MASK = "[masked]"

    def __init__(self, repository: ContactDetectionRepository):
        self.repository = repository

    def detect(self, text: str) -> list[ContactDetectionMatch]:
        matches: list[ContactDetectionMatch] = []

        patterns = [
            ("email", self.EMAIL_RE, 0.98),
            ("telegram_username", self.TELEGRAM_RE, 0.95),
            ("messenger_phone", self.MESSENGER_RE, 0.95),
            ("phone", self.PHONE_RE, 0.90),
            ("external_payment", self.EXTERNAL_PAYMENT_RE, 0.85),
        ]

        for detected_type, pattern, confidence in patterns:
            for match in pattern.finditer(text or ""):
                value = match.group(0).strip()
                if value:
                    matches.append(
                        ContactDetectionMatch(
                            detected_type=detected_type,
                            value=value,
                            confidence=confidence,
                        )
                    )

        return self._deduplicate_matches(matches)

    def mask_text(self, text: str, matches: list[ContactDetectionMatch]) -> str:
        masked_text = text or ""

        for match in sorted(matches, key=lambda item: len(item.value), reverse=True):
            masked_text = masked_text.replace(match.value, self.MASK)

        return masked_text

    async def process_message(self, message_id: UUID) -> ContactDetectionResult:
        message = await self.repository.get_message(message_id)
        if not message:
            return ContactDetectionResult(
                message_id=None,
                is_masked=False,
                detected_types=[],
                action_taken=None,
            )

        matches = self.detect(message.original_text or "")
        if not matches:
            return ContactDetectionResult(
                message_id=message.id,
                is_masked=False,
                detected_types=[],
                action_taken=None,
            )

        detected_types = sorted({match.detected_type for match in matches})
        thread_restricted = "external_payment" in detected_types
        action_taken = (
            "masked_warning_risk_flag_thread_restricted"
            if thread_restricted
            else "masked_warning_risk_flag"
        )

        masked_text = self.mask_text(message.original_text or "", matches)

        await self.repository.mark_message_masked(
            message=message,
            masked_text=masked_text,
            detected_types=detected_types,
            action_taken=action_taken,
        )

        for match in matches:
            await self.repository.log_detection(
                tenant_id=message.tenant_id,
                message_id=message.id,
                detected_type=match.detected_type,
                confidence=match.confidence,
                action_taken=action_taken,
            )

        await self.repository.create_risk_flag(
            tenant_id=message.tenant_id,
            entity_type="message",
            entity_id=message.id,
            flag_code="off_platform_contact",
            severity="high" if thread_restricted else "medium",
            details={
                "detected_types": detected_types,
                "action_taken": action_taken,
                "thread_id": str(message.thread_id),
            },
        )

        if thread_restricted:
            await self.repository.restrict_thread(
                thread_id=message.thread_id,
                reason="off_platform_payment",
            )

        await self.repository.session.commit()

        return ContactDetectionResult(
            message_id=message.id,
            is_masked=True,
            detected_types=detected_types,
            action_taken=action_taken,
            thread_restricted=thread_restricted,
        )

    def _deduplicate_matches(
        self,
        matches: list[ContactDetectionMatch],
    ) -> list[ContactDetectionMatch]:
        seen: set[tuple[str, str]] = set()
        result: list[ContactDetectionMatch] = []

        for match in matches:
            key = (match.detected_type, match.value.lower())
            if key in seen:
                continue
            seen.add(key)
            result.append(match)

        return result