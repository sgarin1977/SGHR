from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.security import (
    EmailAuthChallengeCodec,
)


class ApiEmailAuthRateLimitError(Exception):
    pass


class ApiEmailAuthDeliveryError(Exception):
    pass


class ApiEmailAuthVerificationError(Exception):
    pass


@dataclass(frozen=True)
class ApiEmailChallengeRequest:
    challenge_id: UUID
    challenge_type: str
    expires_at: datetime


class EmailChallengeRepository(Protocol):
    async def count_recent_challenges(
        self,
        *,
        email: str,
        since: datetime,
    ) -> int:
        ...

    async def create_challenge(
        self,
        **kwargs,
    ) -> object:
        ...


class EmailChallengeDelivery(Protocol):
    async def send_challenge(
        self,
        **kwargs,
    ) -> None:
        ...


class EmailIdentityResolver(Protocol):
    async def resolve_verified_email(
        self,
        **kwargs,
    ) -> object:
        ...


class EmailTokenService(Protocol):
    async def prepare_token_pair(
        self,
        **kwargs,
    ) -> object:
        ...


class EmailSecurityEventRepository(Protocol):
    async def create_event(
        self,
        **kwargs,
    ) -> object:
        ...


class ApiEmailAuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        challenge_repository: (
            EmailChallengeRepository
        ),
        challenge_codec: (
            EmailAuthChallengeCodec
        ),
        email_delivery: EmailChallengeDelivery,
        code_ttl_seconds: int,
        max_attempts: int,
        request_limit: int,
        request_window_seconds: int,
        identity_service: (
            EmailIdentityResolver | None
        ) = None,
        token_service: (
            EmailTokenService | None
        ) = None,
        event_repository: (
            EmailSecurityEventRepository
            | None
        ) = None,
    ):
        values = (
            code_ttl_seconds,
            max_attempts,
            request_limit,
            request_window_seconds,
        )

        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
            for value in values
        ):
            raise ValueError(
                "Email Auth policy values "
                "must be positive integers."
            )

        self.session = session
        self.challenge_repository = (
            challenge_repository
        )
        self.challenge_codec = (
            challenge_codec
        )
        self.email_delivery = email_delivery
        self.code_ttl_seconds = (
            code_ttl_seconds
        )
        self.max_attempts = max_attempts
        self.request_limit = request_limit
        self.request_window_seconds = (
            request_window_seconds
        )
        self.identity_service = (
            identity_service
        )
        self.token_service = token_service
        self.event_repository = (
            event_repository
        )

    async def _record_failed_verification(
        self,
        *,
        challenge: object,
        current_time: datetime,
    ) -> None:
        await (
            self.challenge_repository
            .record_failed_attempt(
                challenge=challenge,
                now=current_time,
            )
        )

        if self.event_repository is not None:
            await (
                self.event_repository
                .create_event(
                    event_type=(
                        "api_email_auth_failed"
                    ),
                    tenant_id=(
                        challenge.tenant_id
                    ),
                    user_id=(
                        challenge
                        .requested_user_id
                    ),
                    entity_type=(
                        "api_email_auth_challenge"
                    ),
                    entity_id=challenge.id,
                    payload={
                        "auth_method": "email",
                        "challenge_type": (
                            challenge
                            .challenge_type
                        ),
                        "reason": (
                            "invalid_secret"
                        ),
                    },
                    platform="api",
                )
            )

        await self.session.commit()


    async def _record_missing_challenge(
        self,
        *,
        challenge_id: UUID,
    ) -> None:
        await self.session.rollback()

        if self.event_repository is None:
            return

        try:
            await (
                self.event_repository
                .create_event(
                    event_type=(
                        "api_email_auth_failed"
                    ),
                    tenant_id=None,
                    user_id=None,
                    entity_type=(
                        "api_email_auth_challenge"
                    ),
                    entity_id=challenge_id,
                    payload={
                        "auth_method": "email",
                        "reason": (
                            "challenge_not_found"
                        ),
                    },
                    platform="api",
                )
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()


    async def _complete_verified_challenge(
        self,
        *,
        challenge: object,
        language_code: str,
        current_time: datetime,
    ) -> object:
        if (
            self.identity_service is None
            or self.token_service is None
        ):
            await self.session.rollback()
            raise RuntimeError(
                "Email verification services "
                "are not configured."
            )

        try:
            user = await (
                self.identity_service
                .resolve_verified_email(
                    email=challenge.email,
                    requested_user_id=(
                        challenge
                        .requested_user_id
                    ),
                    requested_tenant_id=(
                        challenge.tenant_id
                    ),
                    language_code=(
                        language_code
                    ),
                )
            )

            token_pair = await (
                self.token_service
                .prepare_token_pair(
                    user=user,
                    auth_method="email",
                    device_id=(
                        challenge.device_id
                    ),
                    device_name=(
                        challenge.device_name
                    ),
                    now=current_time,
                )
            )

            await (
                self.challenge_repository
                .mark_used(
                    challenge=challenge,
                    now=current_time,
                )
            )

            if self.event_repository is not None:
                await (
                    self.event_repository
                    .create_event(
                        event_type=(
                            "api_email_auth_"
                            "succeeded"
                        ),
                        tenant_id=user.tenant_id,
                        user_id=user.id,
                        entity_type=(
                            "api_email_auth_"
                            "challenge"
                        ),
                        entity_id=challenge.id,
                        payload={
                            "auth_method": "email",
                            "challenge_type": (
                                challenge
                                .challenge_type
                            ),
                        },
                        platform="api",
                    )
                )

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return token_pair


    async def request_challenge(
        self,
        *,
        email: str,
        challenge_type: str,
        device_id: str | None,
        device_name: str | None,
        tenant_id: UUID | None = None,
        requested_user_id: UUID | None = None,
        now: datetime | None = None,
    ) -> ApiEmailChallengeRequest:
        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError(
                "Challenge request time must "
                "be timezone-aware."
            )

        current_time = current_time.astimezone(
            UTC
        )
        normalized_email = (
            email.strip().casefold()
            if isinstance(email, str)
            else ""
        )

        if (
            not normalized_email
            or len(normalized_email) > 320
        ):
            raise ValueError(
                "Email address is invalid."
            )

        if challenge_type not in {
            "otp",
            "magic_link",
        }:
            raise ValueError(
                "Challenge type is invalid."
            )

        recent_count = await (
            self.challenge_repository
            .count_recent_challenges(
                email=normalized_email,
                since=(
                    current_time
                    - timedelta(
                        seconds=(
                            self
                            .request_window_seconds
                        )
                    )
                ),
            )
        )

        if recent_count >= self.request_limit:
            raise ApiEmailAuthRateLimitError(
                "Email authentication request "
                "limit exceeded."
            )

        if challenge_type == "otp":
            material = (
                self.challenge_codec.issue_code(
                    email=normalized_email
                )
            )
            secret = material.code
        else:
            material = (
                self.challenge_codec
                .issue_magic_link(
                    email=normalized_email
                )
            )
            secret = material.token

        expires_at = (
            current_time
            + timedelta(
                seconds=self.code_ttl_seconds
            )
        )

        try:
            challenge = await (
                self.challenge_repository
                .create_challenge(
                    tenant_id=tenant_id,
                    requested_user_id=(
                        requested_user_id
                    ),
                    email=normalized_email,
                    challenge_hash=(
                        material.challenge_hash
                    ),
                    challenge_type=(
                        challenge_type
                    ),
                    purpose=(
                        "link_email"
                        if requested_user_id
                        is not None
                        else "login"
                    ),
                    device_id=device_id,
                    device_name=device_name,
                    max_attempts=(
                        self.max_attempts
                    ),
                    expires_at=expires_at,
                )
            )
        except Exception:
            await self.session.rollback()
            raise

        try:
            await self.email_delivery.send_challenge(
                email=normalized_email,
                challenge_id=challenge.id,
                challenge_type=challenge_type,
                secret=secret,
                expires_at=expires_at,
            )
        except Exception as exc:
            await self.session.rollback()
            raise ApiEmailAuthDeliveryError(
                "Email challenge could not "
                "be delivered."
            ) from exc

        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return ApiEmailChallengeRequest(
            challenge_id=challenge.id,
            challenge_type=challenge_type,
            expires_at=expires_at,
        )


    async def verify_challenge(
        self,
        *,
        challenge_id: UUID,
        email: str,
        secret: str,
        language_code: str,
        now: datetime | None = None,
    ) -> object:
        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError(
                "Challenge verification time "
                "must be timezone-aware."
            )

        current_time = current_time.astimezone(
            UTC
        )
        normalized_email = (
            email.strip().casefold()
            if isinstance(email, str)
            else ""
        )

        if (
            not normalized_email
            or len(normalized_email) > 320
            or not isinstance(secret, str)
            or not secret
        ):
            raise ApiEmailAuthVerificationError(
                "Email challenge is invalid."
            )

        challenge = await (
            self.challenge_repository
            .get_pending_challenge_for_update(
                challenge_id=challenge_id,
                email=normalized_email,
                now=current_time,
            )
        )

        if challenge is None:
            await self._record_missing_challenge(
                challenge_id=challenge_id,
            )
            raise ApiEmailAuthVerificationError(
                "Email challenge is invalid."
            )

        if challenge.challenge_type == "otp":
            verified = (
                self.challenge_codec.verify_code(
                    email=normalized_email,
                    code=secret,
                    expected_hash=(
                        challenge.challenge_hash
                    ),
                )
            )
        elif (
            challenge.challenge_type
            == "magic_link"
        ):
            verified = (
                self.challenge_codec
                .verify_magic_link(
                    email=normalized_email,
                    token=secret,
                    expected_hash=(
                        challenge.challenge_hash
                    ),
                )
            )
        else:
            await self.session.rollback()
            raise ApiEmailAuthVerificationError(
                "Email challenge is invalid."
            )

        if not verified:
            await (
                self._record_failed_verification(
                    challenge=challenge,
                    current_time=current_time,
                )
            )

            raise ApiEmailAuthVerificationError(
                "Email challenge is invalid."
            )

        return await (
            self._complete_verified_challenge(
                challenge=challenge,
                language_code=language_code,
                current_time=current_time,
            )
        )


    async def verify_magic_link_token(
        self,
        *,
        token: str,
        language_code: str,
        now: datetime | None = None,
    ) -> object:
        current_time = now or datetime.now(UTC)

        if current_time.tzinfo is None:
            raise ValueError(
                "Magic Link verification time "
                "must be timezone-aware."
            )

        current_time = current_time.astimezone(
            UTC
        )
        normalized_token = (
            token.strip()
            if isinstance(token, str)
            else ""
        )
        challenge_id_value, separator, secret = (
            normalized_token.partition(".")
        )

        if (
            not separator
            or not challenge_id_value
            or not secret
            or len(normalized_token) > 1024
        ):
            raise ApiEmailAuthVerificationError(
                "Email challenge is invalid."
            )

        try:
            challenge_id = UUID(
                challenge_id_value
            )
        except ValueError:
            raise ApiEmailAuthVerificationError(
                "Email challenge is invalid."
            ) from None

        challenge = await (
            self.challenge_repository
            .get_pending_magic_link_for_update(
                challenge_id=challenge_id,
                now=current_time,
            )
        )

        if challenge is None:
            await self._record_missing_challenge(
                challenge_id=challenge_id,
            )
            raise ApiEmailAuthVerificationError(
                "Email challenge is invalid."
            )

        verified = (
            self.challenge_codec
            .verify_magic_link(
                email=challenge.email,
                token=secret,
                expected_hash=(
                    challenge.challenge_hash
                ),
            )
        )

        if not verified:
            await (
                self._record_failed_verification(
                    challenge=challenge,
                    current_time=current_time,
                )
            )

            raise ApiEmailAuthVerificationError(
                "Email challenge is invalid."
            )

        return await (
            self._complete_verified_challenge(
                challenge=challenge,
                language_code=language_code,
                current_time=current_time,
            )
        )

