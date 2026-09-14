from pathlib import Path

import pytest


def test_email_delivery_settings_support_resend():
    from api.settings import (
        ApiEmailDeliverySettings,
    )

    settings = (
        ApiEmailDeliverySettings.from_env(
            {
                "EMAIL_PROVIDER": "resend",
                "EMAIL_FROM": (
                    "auth@notify.example.com"
                ),
                "WEB_APP_URL": (
                    "https://app.example.com"
                ),
                "MOBILE_DEEP_LINK_SCHEME": (
                    "sghr"
                ),
                "RESEND_API_KEY": (
                    "re_test_secret_key"
                ),
            }
        )
    )

    assert settings.provider == "resend"
    assert settings.from_address == (
        "auth@notify.example.com"
    )
    assert settings.web_app_url == (
        "https://app.example.com"
    )
    assert (
        settings.mobile_deep_link_scheme
        == "sghr"
    )
    assert settings.resend_api_key == (
        "re_test_secret_key"
    )


def test_email_delivery_settings_are_documented():
    source = Path(".env.example").read_text(
        encoding="utf-8-sig"
    )

    required = (
        "EMAIL_PROVIDER=resend",
        "EMAIL_FROM=",
        "WEB_APP_URL=",
        "MOBILE_DEEP_LINK_SCHEME=sghr",
        "EMAIL_AUTH_TOKEN_TTL=600",
        "RESEND_API_KEY=",
    )

    for value in required:
        assert value in source


@pytest.mark.asyncio
async def test_magic_link_delivery_is_provider_neutral():
    from datetime import UTC, datetime
    from uuid import uuid4

    from services.email_delivery import (
        ApiEmailChallengeDelivery,
    )

    calls = []

    class FakeEmailProvider:
        async def send_email(
            self,
            **kwargs,
        ):
            calls.append(kwargs)

    delivery = ApiEmailChallengeDelivery(
        provider=FakeEmailProvider(),
        from_address=(
            "auth@notify.example.com"
        ),
        web_app_url=(
            "https://app.example.com"
        ),
        mobile_deep_link_scheme="sghr",
    )

    await delivery.send_challenge(
        email="user@example.com",
        challenge_id=uuid4(),
        challenge_type="magic_link",
        secret="private-magic-token",
        expires_at=datetime(
            2026,
            8,
            28,
            18,
            10,
            tzinfo=UTC,
        ),
    )

    assert len(calls) == 1

    message = calls[0]
    assert message["from_address"] == (
        "auth@notify.example.com"
    )
    assert message["to_address"] == (
        "user@example.com"
    )
    assert "private-magic-token" in (
        message["html_body"]
    )
    assert (
        "https://app.example.com/auth/verify"
        "?token="
        in message["html_body"]
    )
    assert (
        "sghr://auth/verify"
        "?token="
        in message["html_body"]
    )
    assert "resend" not in (
        type(delivery).__module__.lower()
    )


@pytest.mark.asyncio
async def test_resend_provider_uses_shared_http_client():
    from services.resend_email_provider import (
        ResendEmailProvider,
    )

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            calls.append(
                ("raise_for_status",)
            )

    class FakeHttpClient:
        async def post(self, url, **kwargs):
            calls.append(
                (
                    "post",
                    url,
                    kwargs,
                )
            )
            return FakeResponse()

    provider = ResendEmailProvider(
        api_key="re_test_secret_key",
        client=FakeHttpClient(),
        timeout_seconds=10,
    )

    await provider.send_email(
        from_address=(
            "auth@notify.example.com"
        ),
        to_address="user@example.com",
        subject="Sign in to SGHR",
        text_body="Text content",
        html_body="<p>HTML content</p>",
    )

    assert calls[0][0] == "post"
    assert calls[0][1] == (
        "https://api.resend.com/emails"
    )

    request = calls[0][2]
    assert request["headers"] == {
        "Authorization": (
            "Bearer re_test_secret_key"
        ),
        "Content-Type": "application/json",
    }
    assert request["json"] == {
        "from": "auth@notify.example.com",
        "to": ["user@example.com"],
        "subject": "Sign in to SGHR",
        "text": "Text content",
        "html": "<p>HTML content</p>",
    }
    assert request["timeout"] == 10
    assert calls[1] == (
        "raise_for_status",
    )


def test_email_auth_dependency_builds_without_bot_token(
    monkeypatch,
):
    from uuid import uuid4

    from api.auth import (
        get_api_email_auth_service,
    )
    from services.api_email_auth import (
        ApiEmailAuthService,
    )

    monkeypatch.delenv(
        "BOT_TOKEN",
        raising=False,
    )
    monkeypatch.setenv(
        "DEFAULT_TENANT_ID",
        str(uuid4()),
    )
    monkeypatch.setenv(
        "API_JWT_SECRET",
        "j" * 64,
    )
    monkeypatch.setenv(
        "API_EMAIL_AUTH_SECRET",
        "e" * 64,
    )

    session = object()
    delivery = object()
    access_codec = object()
    refresh_codec = object()

    service = get_api_email_auth_service(
        session=session,
        email_delivery=delivery,
        access_token_codec=access_codec,
        refresh_token_codec=refresh_codec,
    )

    assert isinstance(
        service,
        ApiEmailAuthService,
    )
    assert service.session is session
    assert service.email_delivery is delivery
    assert service.identity_service is not None
    assert service.token_service is not None
    assert (
        service.token_service
        .access_token_codec
        is access_codec
    )
    assert (
        service.token_service
        .refresh_token_codec
        is refresh_codec
    )
    assert (
        service.token_service
        .telegram_verifier
        is None
    )


@pytest.mark.asyncio
async def test_magic_link_contains_self_contained_token():
    from datetime import UTC, datetime
    from uuid import uuid4

    from services.email_delivery import (
        ApiEmailChallengeDelivery,
    )

    challenge_id = uuid4()
    calls = []

    class FakeEmailProvider:
        async def send_email(
            self,
            **kwargs,
        ):
            calls.append(kwargs)

    delivery = ApiEmailChallengeDelivery(
        provider=FakeEmailProvider(),
        from_address=(
            "auth@notify.example.com"
        ),
        web_app_url=(
            "https://app.example.com"
        ),
        mobile_deep_link_scheme="sghr",
    )

    await delivery.send_challenge(
        email="user@example.com",
        challenge_id=challenge_id,
        challenge_type="magic_link",
        secret="private-magic-token",
        expires_at=datetime(
            2026,
            8,
            28,
            20,
            30,
            tzinfo=UTC,
        ),
    )

    callback_token = (
        f"{challenge_id}."
        "private-magic-token"
    )
    html_body = calls[0]["html_body"]

    assert (
        "https://app.example.com/auth/verify"
        f"?token={callback_token}"
        in html_body
    )
    assert (
        "sghr://auth/verify"
        f"?token={callback_token}"
        in html_body
    )
    assert "user@example.com" not in html_body
    assert "user%40example.com" not in html_body


@pytest.mark.asyncio
async def test_api_shutdown_closes_resend_http_client(
    monkeypatch,
):
    import api.app as app_module

    calls = []

    async def fake_close():
        calls.append("closed")

    monkeypatch.setattr(
        app_module,
        "close_resend_http_client",
        fake_close,
        raising=False,
    )

    application = app_module.create_app()

    async with (
        application.router.lifespan_context(
            application
        )
    ):
        assert calls == []

    assert calls == ["closed"]
