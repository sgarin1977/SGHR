# Construction invitation email adapter

Stage 1A: CON-C005 / CON-C007. This is a backend integration component,
not confirmation of frontend readiness or deployed email delivery.

## Platform integration

`services.construction.construction_invitation_platform_email` uses the existing
`services.email_delivery.EmailProvider` protocol. Release configuration
uses the existing Resend adapter, EMAIL_FROM, WEB_APP_URL and RESEND_API_KEY.

`build_construction_platform_invitation_email_worker` requires the
server-configured `recipient_page_url`. Its HTTPS origin must match
WEB_APP_URL. No production recipient page path is invented or installed.
Production addresses and credentials remain deployment configuration,
as specified in the final API clarifications.

The factory uses API_IDEMPOTENCY_ENCRYPTION_KEY through the existing
ApiIdempotencySettings for delivery encryption and recipient routing,
matching the recipient API. Encryption purposes remain separated by
the existing codecs.

The caller owns the Construction transaction and worker session.
The locked loader validates the current invitation, current job/token,
lease and pending lifetime before delivery. Stale jobs after resend and
terminal/expired invitations do not reach the provider.

## Recipient frontend contract

The link carries `invitation_context` and `token` in its URL fragment.
The configured frontend page must read them, remove the fragment after
capture and retain them securely through Platform authentication.
They must not be copied into logs, analytics or arbitrary redirect URLs.

Opening the link creates no membership or grant. The frontend calls:

- POST /api/v1/construction/invitations/preview
- POST /api/v1/construction/invitations/accept
- POST /api/v1/construction/invitations/decline

Preview provides the current row_version. Accept requires Idempotency-Key.
Accept and decline require authenticated Platform identity, verified
invitation email and the current token; mutations recheck database state.

## Delivery outcomes

Messages support en, pt and ru; unconfigured language falls back to en.
Invitation expiry is used, independently of Email Auth token TTL.

Explicit job retry/permanent outcomes from a provider are preserved.
Other provider exceptions become EMAIL_DELIVERY_FAILED without private
provider details. Provider responses and secret links are not stored as
queue result references. External email delivery is not a database
transaction and the adapter does not commit or roll back the session.

## Verification boundaries

Tests use mocked SQL sessions, fake providers and httpx.MockTransport.
They exercise material validation, recipient context, template escaping,
fragment contents, worker wiring and the existing provider interface.
The installer performs no Supabase, Storage or real email operations.

Frontend implementation, deployed callback page, scheduler activation
and real provider delivery still require separate verification.
