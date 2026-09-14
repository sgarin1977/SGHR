from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.models import (
    ApiEmailAuthChallenge,
)


class ApiEmailAuthChallengeRepository:
    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def create_challenge(
        self,
        *,
        tenant_id: UUID | None,
        requested_user_id: UUID | None,
        email: str,
        challenge_hash: str,
        challenge_type: str,
        purpose: str,
        device_id: str | None,
        device_name: str | None,
        max_attempts: int,
        expires_at: datetime,
    ) -> ApiEmailAuthChallenge:
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

        if (
            not isinstance(
                challenge_hash,
                str,
            )
            or len(challenge_hash) != 64
            or any(
                character
                not in "0123456789abcdef"
                for character
                in challenge_hash.casefold()
            )
        ):
            raise ValueError(
                "Challenge hash must be a "
                "64-character hexadecimal value."
            )

        if challenge_type not in {
            "otp",
            "magic_link",
        }:
            raise ValueError(
                "Challenge type is invalid."
            )

        if purpose not in {
            "login",
            "link_email",
        }:
            raise ValueError(
                "Challenge purpose is invalid."
            )

        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or max_attempts <= 0
        ):
            raise ValueError(
                "Maximum attempts must be "
                "positive."
            )

        if expires_at.tzinfo is None:
            raise ValueError(
                "Challenge expiration must be "
                "timezone-aware."
            )

        if (
            tenant_id is None
        ) != (
            requested_user_id is None
        ):
            raise ValueError(
                "Challenge actor scope is "
                "incomplete."
            )

        challenge = ApiEmailAuthChallenge(
            tenant_id=tenant_id,
            requested_user_id=(
                requested_user_id
            ),
            email=normalized_email,
            challenge_hash=challenge_hash,
            challenge_type=challenge_type,
            purpose=purpose,
            device_id=device_id,
            device_name=device_name,
            status="pending",
            attempts_count=0,
            max_attempts=max_attempts,
            expires_at=expires_at,
        )

        self.session.add(challenge)
        await self.session.flush()

        return challenge


    async def count_recent_challenges(
        self,
        *,
        email: str,
        since: datetime,
    ) -> int:
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

        if since.tzinfo is None:
            raise ValueError(
                "Rate-limit time must be "
                "timezone-aware."
            )

        statement = (
            select(
                func.count(
                    ApiEmailAuthChallenge.id
                )
            )
            .where(
                ApiEmailAuthChallenge.email
                == normalized_email,
                ApiEmailAuthChallenge.created_at
                >= since,
            )
        )

        result = await self.session.execute(
            statement
        )
        return int(result.scalar_one())


    async def get_pending_challenge_for_update(
        self,
        *,
        challenge_id: UUID,
        email: str,
        now: datetime,
    ) -> ApiEmailAuthChallenge | None:
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

        if not isinstance(
            challenge_id,
            UUID,
        ):
            raise ValueError(
                "Challenge ID is invalid."
            )

        if now.tzinfo is None:
            raise ValueError(
                "Challenge lookup time must "
                "be timezone-aware."
            )

        statement = (
            select(ApiEmailAuthChallenge)
            .where(
                ApiEmailAuthChallenge.email
                == normalized_email,
                ApiEmailAuthChallenge.id
                == challenge_id,
                ApiEmailAuthChallenge.status
                == "pending",
                ApiEmailAuthChallenge.expires_at
                > now,
                ApiEmailAuthChallenge.used_at
                .is_(None),
                ApiEmailAuthChallenge
                .attempts_count
                < ApiEmailAuthChallenge
                .max_attempts,
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()


    async def get_pending_magic_link_for_update(
        self,
        *,
        challenge_id: UUID,
        now: datetime,
    ) -> ApiEmailAuthChallenge | None:
        if not isinstance(
            challenge_id,
            UUID,
        ):
            raise ValueError(
                "Challenge ID is invalid."
            )

        if now.tzinfo is None:
            raise ValueError(
                "Challenge lookup time must "
                "be timezone-aware."
            )

        statement = (
            select(ApiEmailAuthChallenge)
            .where(
                ApiEmailAuthChallenge.id
                == challenge_id,
                ApiEmailAuthChallenge
                .challenge_type
                == "magic_link",
                ApiEmailAuthChallenge.status
                == "pending",
                ApiEmailAuthChallenge.expires_at
                > now,
                ApiEmailAuthChallenge.used_at
                .is_(None),
                ApiEmailAuthChallenge
                .attempts_count
                < ApiEmailAuthChallenge
                .max_attempts,
            )
            .with_for_update()
        )

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()


    async def mark_used(
        self,
        *,
        challenge: ApiEmailAuthChallenge,
        now: datetime,
    ) -> ApiEmailAuthChallenge:
        if now.tzinfo is None:
            raise ValueError(
                "Challenge usage time must be "
                "timezone-aware."
            )

        if challenge.status != "pending":
            raise ValueError(
                "Challenge is not pending."
            )

        challenge.status = "used"
        challenge.used_at = now
        challenge.updated_at = now

        await self.session.flush()
        return challenge

    async def record_failed_attempt(
        self,
        *,
        challenge: ApiEmailAuthChallenge,
        now: datetime,
    ) -> ApiEmailAuthChallenge:
        if now.tzinfo is None:
            raise ValueError(
                "Challenge attempt time must be "
                "timezone-aware."
            )

        if challenge.status != "pending":
            raise ValueError(
                "Challenge is not pending."
            )

        if (
            challenge.attempts_count
            >= challenge.max_attempts
        ):
            raise ValueError(
                "Challenge has no attempts left."
            )

        challenge.attempts_count += 1
        challenge.updated_at = now

        if (
            challenge.attempts_count
            >= challenge.max_attempts
        ):
            challenge.status = "locked"

        await self.session.flush()
        return challenge
