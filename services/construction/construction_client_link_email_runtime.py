"""Explicit PostgreSQL worker for client-link confirmations.

Claim, locked validation, delivery and queue outcome share one transaction.
External email can be redelivered after a crash before commit; any retry uses
the same protected link and does not renew or recreate the confirmation.
"""
import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import or_, select, text

from api.settings import ApiEmailDeliverySettings, ApiIdempotencySettings
from database.models import ConstructionBackgroundJobRecord
from database.repositories.job_queue import PostgresJobQueue
from services.construction.construction_client_link_email_worker import (
    JOB_TYPE, LANGUAGES, build_client_link_email_handler,
)
from services.construction.construction_invitation_platform_email import require_invitation_page_url
from services.construction.construction_invitation_worker_runtime import (
    _seconds, verify_invitation_worker_connection,
)
from services.construction.construction_jobs import ConstructionBackgroundJobWorker

@dataclass(frozen=True)
class ClientLinkEmailSettings:
    confirmation_page_url: str
    poll_seconds: float
    lease_seconds: float
    job_timeout_seconds: float
    language_code: str
    email: ApiEmailDeliverySettings = field(repr=False)
    crypto: ApiIdempotencySettings = field(repr=False)

    @classmethod
    def from_env(cls, environ=None):
        if environ is None:
            from dotenv import load_dotenv
            load_dotenv()
            environ = os.environ
        email = ApiEmailDeliverySettings.from_env(environ)
        crypto = ApiIdempotencySettings.from_env(environ)
        page = require_invitation_page_url(
            environ.get("CONSTRUCTION_CLIENT_LINK_CONFIRMATION_PAGE_URL", ""),
        )
        web = require_invitation_page_url(email.web_app_url)
        parsed_page, parsed_web = urlsplit(page), urlsplit(web)
        if (
            parsed_page.hostname, parsed_page.port or 443
        ) != (parsed_web.hostname, parsed_web.port or 443):
            raise ValueError("Confirmation page must use the Platform frontend origin.")
        poll = _seconds(environ, "CONSTRUCTION_CLIENT_LINK_WORKER_POLL_SECONDS", 1, 60)
        lease = _seconds(environ, "CONSTRUCTION_CLIENT_LINK_WORKER_LEASE_SECONDS", 120, 3600)
        timeout = _seconds(environ, "CONSTRUCTION_CLIENT_LINK_WORKER_JOB_TIMEOUT_SECONDS", 30, 300)
        if lease < timeout + 10:
            raise ValueError("Worker lease must exceed the job timeout.")
        language = environ.get("CONSTRUCTION_CLIENT_LINK_EMAIL_LANGUAGE", "en")
        if language not in LANGUAGES:
            raise ValueError("Unsupported client-link email language.")
        return cls(page, poll, lease, timeout, language, email, crypto)

class ClientLinkEmailQueue(PostgresJobQueue):
    def __init__(self, session):
        super().__init__(session, model=ConstructionBackgroundJobRecord)

    async def claim_next(self, *, now, lease_owner, lease_expires_at):
        if (
            not isinstance(now, datetime) or now.tzinfo is None
            or now.utcoffset() is None
            or not isinstance(lease_expires_at, datetime)
            or lease_expires_at.tzinfo is None
            or lease_expires_at.utcoffset() is None
            or lease_expires_at <= now
            or not isinstance(lease_owner, str) or not lease_owner.strip()
            or self.session.in_transaction() is not True
        ):
            raise ValueError("Email claim requires an active transaction and valid lease.")
        model = ConstructionBackgroundJobRecord
        statement = select(model).where(
            model.job_type == JOB_TYPE,
            model.status.in_(("QUEUED", "RETRY", "RUNNING")),
            model.available_at <= now,
            or_(
                model.lease_expires_at.is_(None),
                model.lease_expires_at <= now,
            ),
        ).order_by(model.available_at, model.id).limit(1).with_for_update(
            skip_locked=True,
        ).execution_options(populate_existing=True)
        with self.session.no_autoflush:
            job = (await self.session.execute(statement)).scalar_one_or_none()
        if job is None:
            return None
        job.status, job.lease_owner = "RUNNING", lease_owner
        job.attempts += 1
        job.lease_expires_at = lease_expires_at
        await self.session.flush()
        return job

def build_client_link_runtime_worker(
    *, session, settings, lease_owner, clock=None, provider=None,
):
    from services.resend_email_provider import ResendEmailProvider
    clock = clock or (lambda: datetime.now(timezone.utc))
    provider = provider if provider is not None else ResendEmailProvider(
        api_key=settings.email.resend_api_key,
    )
    handler = build_client_link_email_handler(
        session=session, encryption_key=settings.crypto.encryption_key,
        lease_owner=lease_owner, clock=clock, provider=provider,
        from_address=settings.email.from_address,
        confirmation_page_url=settings.confirmation_page_url,
        language_code=settings.language_code,
    )
    return ConstructionBackgroundJobWorker(
        queue=ClientLinkEmailQueue(session),
        handlers={JOB_TYPE: handler}, lease_owner=lease_owner,
        lease_duration=timedelta(seconds=settings.lease_seconds), clock=clock,
    )

async def verify_client_link_worker_connection(session):
    await verify_invitation_worker_connection(session)
    isolation = (await session.execute(text(
        "SELECT current_setting('transaction_isolation')"
    ))).scalar_one()
    if isolation != "read committed":
        raise ValueError("Client-link worker requires PostgreSQL READ COMMITTED.")

async def process_client_link_email_once(
    *, session_factory, settings, lease_owner,
    worker_factory=build_client_link_runtime_worker,
    verify_connection=verify_client_link_worker_connection,
):
    async with session_factory() as session:
        async with session.begin():
            await verify_connection(session)
            worker = worker_factory(
                session=session, settings=settings, lease_owner=lease_owner,
            )
            result = await asyncio.wait_for(
                worker.run_once(), timeout=settings.job_timeout_seconds,
            )
            if result is not None and not isinstance(result, UUID):
                raise ValueError("Email worker returned an invalid job receipt.")
        return result

async def run_client_link_email_worker(
    *, session_factory, settings, stop, once=False, lease_owner=None,
):
    owner = lease_owner or ("client-link-email:" + str(uuid4()))
    processed = 0
    while not stop.is_set():
        job_id = await process_client_link_email_once(
            session_factory=session_factory, settings=settings,
            lease_owner=owner,
        )
        if job_id is not None:
            processed += 1
        if once:
            return processed
        if job_id is None and not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)
            except TimeoutError:
                pass
    return processed

# client_link_email_worker_v1
