import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken


class ConstructionInvitationDeliveryTokenError(ValueError):
    pass


class ConstructionInvitationTokenCodec:
    PURPOSE = "construction.invitation.delivery"

    def __init__(self, *, encryption_key: str):
        try:
            if not isinstance(encryption_key, str) or not encryption_key:
                raise ValueError
            self._fernet = Fernet(encryption_key.encode("ascii"))
        except (TypeError, ValueError, UnicodeError):
            raise ConstructionInvitationDeliveryTokenError(
                "Invitation delivery encryption key is invalid."
            ) from None

    @staticmethod
    def _require_scope(*, tenant_id, invitation_id):
        if not isinstance(tenant_id, UUID) or not isinstance(invitation_id, UUID):
            raise ConstructionInvitationDeliveryTokenError(
                "Invitation delivery token is not available."
            )

    def encrypt(self, *, tenant_id, invitation_id, token):
        self._require_scope(tenant_id=tenant_id, invitation_id=invitation_id)
        if not isinstance(token, str) or not token:
            raise ConstructionInvitationDeliveryTokenError(
                "Invitation delivery token is not available."
            )
        payload = {
            "purpose": self.PURPOSE,
            "tenant_id": str(tenant_id),
            "invitation_id": str(invitation_id),
            "token": token,
        }
        serialized = json.dumps(
            payload, ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return self._fernet.encrypt(serialized).decode("ascii")

    def decrypt(self, *, ciphertext, tenant_id, invitation_id):
        self._require_scope(tenant_id=tenant_id, invitation_id=invitation_id)
        message = "Invitation delivery token is not available."
        try:
            if not isinstance(ciphertext, str) or not ciphertext:
                raise ValueError
            serialized = self._fernet.decrypt(ciphertext.encode("ascii"))
            payload = json.loads(serialized.decode("utf-8"))
        except (InvalidToken, TypeError, ValueError, UnicodeError):
            raise ConstructionInvitationDeliveryTokenError(message) from None

        if (
            not isinstance(payload, dict)
            or set(payload) != {"purpose", "tenant_id", "invitation_id", "token"}
            or payload["purpose"] != self.PURPOSE
            or payload["tenant_id"] != str(tenant_id)
            or payload["invitation_id"] != str(invitation_id)
            or not isinstance(payload["token"], str)
            or not payload["token"]
        ):
            raise ConstructionInvitationDeliveryTokenError(message)
        return payload["token"]


class ConstructionInvitationDeliveryQueue:
    def __init__(
        self, *, session, queue, invitation_repository,
        token_codec, now_provider=None,
    ):
        from datetime import datetime, timezone

        if (
            session is None
            or getattr(queue, "session", None) is not session
            or getattr(invitation_repository, "session", None) is not session
            or not callable(getattr(token_codec, "encrypt", None))
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Invitation delivery requires valid shared-session wiring.")
        self.session = session
        self.queue = queue
        self.invitation_repository = invitation_repository
        self.token_codec = token_codec
        self.now_provider = (
            now_provider if now_provider is not None
            else lambda: datetime.now(timezone.utc)
        )

    async def enqueue_invitation(self, *, actor, invitation, token, trace_id=None):
        from uuid import UUID, uuid4
        from database.models import ConstructionBackgroundJobRecord
        from services.construction.construction_invitations import (
            ConstructionInvitationOperationError,
            require_construction_invitation_token,
        )

        message = "Invitation delivery context is not available."
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or getattr(invitation, "tenant_id", None) != actor.tenant_id
            or not isinstance(getattr(invitation, "id", None), UUID)
            or not isinstance(getattr(invitation, "delivery_metadata", None), dict)
            or (trace_id is not None and not isinstance(trace_id, str))
        ):
            raise ConstructionInvitationOperationError(message)
        now = self.now_provider()
        require_construction_invitation_token(
            invitation=invitation, token=token, as_of=now,
        )
        ciphertext = self.token_codec.encrypt(
            tenant_id=actor.tenant_id, invitation_id=invitation.id, token=token,
        )
        metadata = dict(invitation.delivery_metadata)
        job = ConstructionBackgroundJobRecord(
            id=uuid4(), tenant_id=actor.tenant_id, user_id=actor.user_id,
            resource_type="construction_invitation", resource_id=invitation.id,
            job_type="INVITATION_EMAIL", status="QUEUED",
            idempotency_key=f"invitation:{invitation.id}:{invitation.token_hash}",
            attempts=0, available_at=now, progress=0,
            lease_owner=None, lease_expires_at=None,
            error_code=None, result_reference=None,
            created_at=now, updated_at=now,
        )
        job_id = await self.queue.enqueue(job=job)
        if not isinstance(job_id, UUID):
            raise ConstructionInvitationOperationError(message)
        metadata.update(
            token_ciphertext=ciphertext, job_id=str(job_id), trace_id=trace_id,
        )
        await self.invitation_repository.update_delivery_metadata(
            invitation=invitation, delivery_metadata=metadata,
        )
        return job_id


def build_construction_invitation_delivery_queue(
    *, session, encryption_key: str, now_provider=None,
):
    from database.repositories.construction import ConstructionInvitationRepository
    from services.construction.construction_jobs import build_construction_job_queue

    return ConstructionInvitationDeliveryQueue(
        session=session,
        queue=build_construction_job_queue(session=session),
        invitation_repository=ConstructionInvitationRepository(session),
        token_codec=ConstructionInvitationTokenCodec(encryption_key=encryption_key),
        now_provider=now_provider,
    )
