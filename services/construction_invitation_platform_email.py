from datetime import datetime
from html import escape
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from services.email_delivery import EmailProvider
from services.construction_invitation_recipient_context import ConstructionInvitationRecipientContextCodec
from services.construction_invitation_recipient_email import ConstructionInvitationRecipientEmailMaterial
from services.construction_invitations import normalize_construction_invitation_email
from services.construction_jobs import ConstructionJobPermanentError, ConstructionJobRetryError

def require_invitation_page_url(value):
    try:
        if not isinstance(value, str) or not value or any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or "\\" in value
        ):
            raise ValueError
        parsed.port
    except (TypeError, ValueError):
        raise ValueError("Invitation recipient page must be a configured HTTPS URL without credentials, query or fragment.") from None
    return value

class ConstructionPlatformInvitationEmailDelivery:
    """Send via the Platform EmailProvider; the page reads the URL fragment.

    The frontend must retain context/token through Platform sign-in and call
    recipient preview/accept/decline APIs. Opening the link grants no access.
    This service never changes invitation, membership, grants or queue state.
    """
    def __init__(
        self, *, provider: EmailProvider, from_address, recipient_page_url,
        recipient_context_encryption_key, clock, language_code="en",
    ):
        if not callable(getattr(provider, "send_email", None)) or not callable(clock):
            raise ValueError("Platform email provider and clock are required.")
        self.provider = provider
        self.from_address = normalize_construction_invitation_email(from_address)
        self.recipient_page_url = require_invitation_page_url(recipient_page_url)
        self.codec = ConstructionInvitationRecipientContextCodec(encryption_key=recipient_context_encryption_key)
        self.clock = clock
        self.language_code = language_code if language_code in ("en", "pt", "ru") else "en"

    async def send_invitation(self, *, material, job_id):
        try:
            now = self.clock()
            if (
                not isinstance(job_id, UUID) or not isinstance(material, ConstructionInvitationRecipientEmailMaterial)
                or not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None
                or not isinstance(material.expires_at, datetime) or material.expires_at.tzinfo is None
                or material.expires_at.utcoffset() is None or material.expires_at <= now
            ):
                raise ValueError
            context = self.codec.resolve(invitation_context=material.invitation_context, token=material.token)
            if context.tenant_id != material.tenant_id or context.invitation_id != material.invitation_id:
                raise ValueError
            email = normalize_construction_invitation_email(material.email)
        except Exception:
            raise ConstructionJobPermanentError(error_code="INVITATION_DELIVERY_CONTEXT_INVALID") from None
        link = self.recipient_page_url + "#" + urlencode({
            "invitation_context": material.invitation_context, "token": material.token,
        })
        messages = {
            "en": (
                "SGHR Construction invitation",
                "You have been invited to SGHR Construction.",
                "Open the invitation, sign in and verify the email address that received this message.",
                "Then review the invitation and choose whether to accept or decline.",
                "Invitation expires at",
                "Review invitation",
            ),
            "pt": (
                "Convite para o SGHR Construction",
                "Recebeu um convite para o SGHR Construction.",
                "Abra o convite, inicie sessão e confirme o endereço de email que recebeu esta mensagem.",
                "Depois, consulte o convite e escolha aceitar ou recusar.",
                "O convite expira em",
                "Consultar convite",
            ),
            "ru": (
                "Приглашение в SGHR Construction",
                "Вас пригласили в SGHR Construction.",
                "Откройте приглашение, войдите и подтвердите email, на который пришло это письмо.",
                "Затем ознакомьтесь с приглашением и выберите принять или отклонить.",
                "Приглашение действует до",
                "Посмотреть приглашение",
            ),
        }
        subject, intro, identity, action, expiry, label = messages[self.language_code]
        deadline = material.expires_at.isoformat()
        text_body = "\n\n".join((intro, identity, action, link, expiry + ": " + deadline))
        html_body = (
            "<p>" + escape(intro) + "</p><p>" + escape(identity) + "</p><p>" + escape(action)
            + '</p><p><a href="' + escape(link, quote=True) + '">' + escape(label)
            + "</a></p><p>" + escape(expiry + ": " + deadline) + "</p>"
        )
        try:
            await self.provider.send_email(
                from_address=self.from_address, to_address=email, subject=subject,
                text_body=text_body, html_body=html_body,
            )
        except (ConstructionJobRetryError, ConstructionJobPermanentError):
            raise
        except Exception:
            raise ConstructionJobPermanentError(error_code="EMAIL_DELIVERY_FAILED") from None

def build_construction_platform_invitation_email_worker(
    *, session, recipient_page_url, lease_owner, lease_duration, clock,
    email_settings=None, crypto_settings=None, provider=None, handlers=None, language_code="en",
):
    from api.settings import ApiEmailDeliverySettings, ApiIdempotencySettings
    from services.resend_email_provider import ResendEmailProvider
    from services.construction_invitation_email_handler import build_construction_invitation_email_worker

    email_settings = email_settings if email_settings is not None else ApiEmailDeliverySettings.from_env()
    crypto_settings = crypto_settings if crypto_settings is not None else ApiIdempotencySettings.from_env()
    recipient_page_url = require_invitation_page_url(recipient_page_url)
    web_url = require_invitation_page_url(email_settings.web_app_url)
    page, web = urlsplit(recipient_page_url), urlsplit(web_url)
    if (page.hostname, page.port or 443) != (web.hostname, web.port or 443):
        raise ValueError("Invitation recipient page must use the configured Platform frontend origin.")
    if email_settings.provider != "resend":
        raise ValueError("Unsupported Platform email provider configuration.")
    provider = provider if provider is not None else ResendEmailProvider(api_key=email_settings.resend_api_key)
    delivery = ConstructionPlatformInvitationEmailDelivery(
        provider=provider, from_address=email_settings.from_address,
        recipient_page_url=recipient_page_url,
        recipient_context_encryption_key=crypto_settings.encryption_key,
        clock=clock, language_code=language_code,
    )
    return build_construction_invitation_email_worker(
        session=session, encryption_key=crypto_settings.encryption_key,
        recipient_context_encryption_key=crypto_settings.encryption_key,
        send_invitation=delivery.send_invitation,
        lease_owner=lease_owner, lease_duration=lease_duration, clock=clock, handlers=handlers,
    )
