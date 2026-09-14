import httpx


RESEND_EMAIL_API_URL = (
    "https://api.resend.com/emails"
)

_resend_http_client: (
    httpx.AsyncClient | None
) = None


class ResendEmailProviderError(Exception):
    pass


def get_resend_http_client(
) -> httpx.AsyncClient:
    global _resend_http_client

    if (
        _resend_http_client is None
        or _resend_http_client.is_closed
    ):
        _resend_http_client = (
            httpx.AsyncClient()
        )

    return _resend_http_client


async def close_resend_http_client(
) -> None:
    global _resend_http_client

    client = _resend_http_client
    _resend_http_client = None

    if (
        client is not None
        and not client.is_closed
    ):
        await client.aclose()


class ResendEmailProvider:
    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10,
    ):
        normalized_key = api_key.strip()

        if not normalized_key.startswith("re_"):
            raise ValueError(
                "Resend API key is invalid."
            )

        if timeout_seconds <= 0:
            raise ValueError(
                "Resend timeout must be positive."
            )

        self.api_key = normalized_key
        self.client = client
        self.timeout_seconds = float(
            timeout_seconds
        )

    async def send_email(
        self,
        *,
        from_address: str,
        to_address: str,
        subject: str,
        text_body: str,
        html_body: str,
    ) -> None:
        client = (
            self.client
            or get_resend_http_client()
        )

        try:
            response = await client.post(
                RESEND_EMAIL_API_URL,
                headers={
                    "Authorization": (
                        f"Bearer {self.api_key}"
                    ),
                    "Content-Type": (
                        "application/json"
                    ),
                },
                json={
                    "from": from_address,
                    "to": [to_address],
                    "subject": subject,
                    "text": text_body,
                    "html": html_body,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ResendEmailProviderError(
                "Resend email delivery failed."
            ) from exc
