from datetime import datetime
from html import escape
from typing import Protocol
from urllib.parse import quote
from uuid import UUID


class EmailProvider(Protocol):
    async def send_email(
        self,
        *,
        from_address: str,
        to_address: str,
        subject: str,
        text_body: str,
        html_body: str,
    ) -> None:
        ...


class ApiEmailChallengeDelivery:
    def __init__(
        self,
        *,
        provider: EmailProvider,
        from_address: str,
        web_app_url: str,
        mobile_deep_link_scheme: str,
    ):
        self.provider = provider
        self.from_address = (
            from_address.strip()
        )
        self.web_app_url = (
            web_app_url.rstrip("/")
        )
        self.mobile_deep_link_scheme = (
            mobile_deep_link_scheme
            .strip()
            .lower()
            .rstrip(":/")
        )

    async def send_challenge(
        self,
        *,
        email: str,
        challenge_id: UUID,
        challenge_type: str,
        secret: str,
        expires_at: datetime,
    ) -> None:
        if expires_at.tzinfo is None:
            raise ValueError(
                "Challenge expiration must "
                "be timezone-aware."
            )

        normalized_email = (
            email.strip().casefold()
        )

        if challenge_type == "otp":
            subject = "SGHR verification code"
            text_body = (
                f"Your SGHR verification code: "
                f"{secret}\n\n"
                f"It expires at "
                f"{expires_at.isoformat()}."
            )
            html_body = (
                "<p>Your SGHR verification "
                "code:</p>"
                f"<p><strong>{escape(secret)}"
                "</strong></p>"
                "<p>This code is temporary "
                "and can be used only once.</p>"
            )
        elif challenge_type == "magic_link":
            callback_token = (
                f"{challenge_id}.{secret}"
            )
            encoded_token = quote(
                callback_token,
                safe="",
            )
            web_link = (
                f"{self.web_app_url}"
                "/auth/verify"
                f"?token={encoded_token}"
            )
            mobile_link = (
                f"{self.mobile_deep_link_scheme}"
                "://auth/verify"
                f"?token={encoded_token}"
            )

            subject = "Sign in to SGHR"
            text_body = (
                "Open this link to sign in:\n"
                f"{web_link}\n\n"
                "Mobile link:\n"
                f"{mobile_link}\n\n"
                "The link is temporary and "
                "can be used only once."
            )
            html_body = (
                "<p>Use this secure link to "
                "sign in to SGHR:</p>"
                f'<p><a href="{escape(web_link)}">'
                "Continue in Web</a></p>"
                f'<p><a href="{escape(mobile_link)}">'
                "Continue in Mobile</a></p>"
                "<p>The link is temporary and "
                "can be used only once.</p>"
            )
        else:
            raise ValueError(
                "Challenge type is invalid."
            )

        await self.provider.send_email(
            from_address=self.from_address,
            to_address=normalized_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
