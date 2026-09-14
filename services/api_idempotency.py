import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet

from database.repositories.api_idempotency import (
    ApiIdempotencyRepository,
)


def hash_request_payload(
    payload: Any,
) -> str:
    canonical_payload = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(
        canonical_payload
    ).hexdigest()



IDEMPOTENCY_RETENTION = timedelta(hours=24)


class ApiIdempotencyKeyReusedError(Exception):
    pass


@dataclass(frozen=True)
class ApiIdempotencyReservation:
    record: Any
    is_replay: bool
    response_status: int | None = None
    response_payload: Any = None


class ApiIdempotencyService:
    def __init__(
        self,
        *,
        repository: Any,
        response_codec: Any | None = None,
    ):
        self.repository = repository
        self.response_codec = response_codec

    async def reserve(
        self,
        *,
        tenant_id: Any,
        principal_type: str,
        principal_id: Any,
        operation: str,
        idempotency_key: str,
        payload: Any,
        now: datetime | None = None,
    ) -> ApiIdempotencyReservation:
        current_time = now or datetime.now(UTC)
        request_hash = hash_request_payload(payload)
        key_hash = hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()

        record = await (
            self.repository
            .get_active_record_for_update(
                tenant_id=tenant_id,
                principal_type=principal_type,
                principal_id=principal_id,
                operation=operation,
                key_hash=key_hash,
                now=current_time,
            )
        )

        if record is not None:
            if record.request_hash != request_hash:
                raise ApiIdempotencyKeyReusedError(
                    "Idempotency key was reused "
                    "with a different request."
                )

            if record.status == "completed":
                if self.response_codec is None:
                    raise ValueError(
                        "Idempotency response codec "
                        "is not configured."
                    )

                return ApiIdempotencyReservation(
                    record=record,
                    is_replay=True,
                    response_status=(
                        record.response_status
                    ),
                    response_payload=(
                        self.response_codec.decrypt(
                            record.response_ciphertext
                        )
                    ),
                )

            return ApiIdempotencyReservation(
                record=record,
                is_replay=True,
            )

        record = await self.repository.create_record(
            tenant_id=tenant_id,
            principal_type=principal_type,
            principal_id=principal_id,
            operation=operation,
            key_hash=key_hash,
            request_hash=request_hash,
            expires_at=(
                current_time
                + IDEMPOTENCY_RETENTION
            ),
        )
        return ApiIdempotencyReservation(
            record=record,
            is_replay=False,
        )



    async def complete(
        self,
        *,
        reservation: ApiIdempotencyReservation,
        response_status: int,
        response_payload: Any,
        now: datetime | None = None,
    ) -> Any:
        if reservation.is_replay:
            raise ValueError(
                "A replay reservation cannot "
                "be completed again."
            )
        if self.response_codec is None:
            raise ValueError(
                "Idempotency response codec "
                "is not configured."
            )

        completed_at = now or datetime.now(UTC)
        response_ciphertext = (
            self.response_codec.encrypt(
                response_payload
            )
        )

        return await self.repository.complete_record(
            record=reservation.record,
            response_status=response_status,
            response_ciphertext=response_ciphertext,
            completed_at=completed_at,
        )


    async def commit(self) -> None:
        await self.repository.session.commit()


class ApiIdempotencyResponseCodec:
    def __init__(
        self,
        *,
        encryption_key: str,
    ):
        if not encryption_key:
            raise ValueError(
                "Idempotency encryption key is missing."
            )

        try:
            self._fernet = Fernet(
                encryption_key.encode("ascii")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Idempotency encryption key is invalid."
            ) from exc

    def encrypt(
        self,
        payload: Any,
    ) -> bytes:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._fernet.encrypt(serialized)

    def decrypt(
        self,
        ciphertext: bytes,
    ) -> Any:
        serialized = self._fernet.decrypt(
            ciphertext
        )
        return json.loads(
            serialized.decode("utf-8")
        )



def build_api_idempotency_service(
    session: Any,
    *,
    encryption_key: str,
) -> ApiIdempotencyService:
    return ApiIdempotencyService(
        repository=ApiIdempotencyRepository(
            session
        ),
        response_codec=ApiIdempotencyResponseCodec(
            encryption_key=encryption_key
        ),
    )
