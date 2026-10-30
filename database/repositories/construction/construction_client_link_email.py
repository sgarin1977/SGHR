"""Bounded exact verified-identity projection using the Platform read session."""
from uuid import UUID

from sqlalchemy import or_, select

from database.models import ApiEmailAuthChallenge, EventLog, User, UserAccount
from services.construction.construction_client_link_email import normalize_client_link_email

class ConstructionClientLinkEmailRepository:
    def __init__(self, session):
        if (
            session is None
            or not callable(getattr(session, "execute", None))
            or not hasattr(session, "no_autoflush")
        ):
            raise ValueError("A Platform read session is required.")
        self.session = session

    async def find_verified_user_id(self, *, email):
        if normalize_client_link_email(email) != email:
            raise ValueError("A normalized exact email is required.")

        # Same OTP/magic-link proof contract as UserRepository.has_verified_email.
        # An email copied into a Telegram profile or client card is not proof.
        proof = (
            select(UserAccount.id)
            .join(EventLog, EventLog.user_id == UserAccount.user_id)
            .join(
                ApiEmailAuthChallenge,
                ApiEmailAuthChallenge.id == EventLog.entity_id,
            )
            .where(
                UserAccount.user_id == User.id,
                UserAccount.platform == "email",
                UserAccount.status == "active",
                UserAccount.email == email,
                UserAccount.platform_user_id == email,
                EventLog.event_type == "api_email_auth_succeeded",
                EventLog.entity_type == "api_email_auth_challenge",
                EventLog.platform == "api",
                ApiEmailAuthChallenge.email == email,
                ApiEmailAuthChallenge.status == "used",
                ApiEmailAuthChallenge.used_at.is_not(None),
                ApiEmailAuthChallenge.challenge_type.in_(("otp", "magic_link")),
                or_(
                    ApiEmailAuthChallenge.requested_user_id.is_(None),
                    ApiEmailAuthChallenge.requested_user_id == User.id,
                ),
            )
            .correlate(User)
        )
        statement = (
            select(User.id)
            .where(User.status == "active", proof.exists())
            .distinct()
            .limit(2)
            .execution_options(autoflush=False)
        )
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
            identifiers = result.scalars().all()

        # Missing or ambiguous identity must never select an arbitrary user.
        if len(identifiers) != 1:
            return None
        if not isinstance(identifiers[0], UUID):
            raise ValueError("Canonical email identity projection is invalid.")
        return identifiers[0]

# client_link_exact_verified_email_v1
