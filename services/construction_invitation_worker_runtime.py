"""Dedicated invitation delivery runtime using the existing PostgreSQL queue.

Each job keeps its claim/invitation locks through delivery and one commit.
A crash after external delivery but before commit can cause redelivery;
this runtime does not claim exactly-once external email delivery.
"""
import asyncio
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import or_, select, text
from api.settings import ApiEmailDeliverySettings, ApiIdempotencySettings
from database.models import ConstructionBackgroundJobRecord
from database.repositories.job_queue import PostgresJobQueue
from services.construction_jobs import ConstructionBackgroundJobWorker
from services.construction_invitation_platform_email import (
    build_construction_platform_invitation_email_worker, require_invitation_page_url,
)

def _seconds(values, name, default, maximum):
    try:
        result = float(values.get(name, str(default)))
    except (TypeError, ValueError):
        raise ValueError(name + ' must be a positive number.') from None
    if not math.isfinite(result) or not 0 < result <= maximum:
        raise ValueError(name + ' is outside its supported range.')
    return result

@dataclass(frozen=True)
class InvitationWorkerSettings:
    recipient_page_url: str
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
        page = require_invitation_page_url(environ.get('CONSTRUCTION_INVITATION_RECIPIENT_PAGE_URL', ''))
        frontend = require_invitation_page_url(email.web_app_url)
        parsed, web = urlsplit(page), urlsplit(frontend)
        if (parsed.hostname, parsed.port or 443) != (web.hostname, web.port or 443):
            raise ValueError('Recipient page must use the configured Platform frontend origin.')
        poll = _seconds(environ, 'CONSTRUCTION_INVITATION_WORKER_POLL_SECONDS', 1, 60)
        lease = _seconds(environ, 'CONSTRUCTION_INVITATION_WORKER_LEASE_SECONDS', 120, 3600)
        timeout = _seconds(environ, 'CONSTRUCTION_INVITATION_WORKER_JOB_TIMEOUT_SECONDS', 30, 300)
        if lease < timeout + 10:
            raise ValueError('Worker lease must exceed job timeout by at least ten seconds.')
        language = environ.get('CONSTRUCTION_INVITATION_EMAIL_LANGUAGE', 'en')
        if language not in ('en', 'pt', 'ru'):
            raise ValueError('Invitation email language must be en, pt or ru.')
        return cls(page, poll, lease, timeout, language, email, crypto)

class InvitationEmailQueue(PostgresJobQueue):
    """Reuse shared queue storage/outcomes, but claim only invitation jobs."""
    def __init__(self, session):
        super().__init__(session, model=ConstructionBackgroundJobRecord)

    async def claim_next(self, *, now, lease_owner, lease_expires_at):
        if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None
                or not isinstance(lease_expires_at, datetime) or lease_expires_at.tzinfo is None
                or lease_expires_at.utcoffset() is None or lease_expires_at <= now
                or not isinstance(lease_owner, str) or not lease_owner.strip()
                or self.session.in_transaction() is not True):
            raise ValueError('Invitation job claim requires an active transaction and valid lease.')
        model = ConstructionBackgroundJobRecord
        statement = select(model).where(
            model.job_type == 'INVITATION_EMAIL',
            model.status.in_(('QUEUED', 'RETRY', 'RUNNING')), model.available_at <= now,
            or_(model.lease_expires_at.is_(None), model.lease_expires_at <= now)
        ).order_by(model.available_at, model.id).limit(1).with_for_update(skip_locked=True).execution_options(
            populate_existing=True)
        with self.session.no_autoflush:
            job = (await self.session.execute(statement)).scalar_one_or_none()
        if job is None:
            return None
        job.status, job.lease_owner = 'RUNNING', lease_owner
        job.attempts += 1
        job.lease_expires_at = lease_expires_at
        await self.session.flush()
        return job

def build_invitation_runtime_worker(*, session, settings, lease_owner, clock=None, provider=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    # Keep the approved loader, recipient envelope, templates and Platform
    # provider adapter. Only the worker's claim scope is narrowed here.
    configured = build_construction_platform_invitation_email_worker(
        session=session, recipient_page_url=settings.recipient_page_url,
        lease_owner=lease_owner, lease_duration=timedelta(seconds=settings.lease_seconds), clock=clock,
        email_settings=settings.email, crypto_settings=settings.crypto,
        language_code=settings.language_code, provider=provider)
    return ConstructionBackgroundJobWorker(queue=InvitationEmailQueue(session), handlers=configured.handlers,
        lease_owner=lease_owner, lease_duration=timedelta(seconds=settings.lease_seconds), clock=clock)

async def verify_invitation_worker_connection(session):
    # Internal Platform background connection, never a browser/Construction
    # API connection. No SET ROLE, new grants or RLS changes are performed.
    row = (await session.execute(text("SELECT current_user AS role_name, current_setting('transaction_read_only') AS read_only"))).mappings().one()
    if (not isinstance(row['role_name'], str) or not row['role_name']
            or row['role_name'] in ('anon', 'authenticated', 'service_role', 'construction_api')
            or row['read_only'] != 'off'):
        raise ValueError('Invitation worker requires its existing internal writable Platform connection.')

async def process_invitation_once(*, session_factory, settings, lease_owner,
    worker_factory=build_invitation_runtime_worker):
    # An exception, cancellation or timeout leaves the context via rollback.
    # There is no commit between claim, locked delivery and queue outcome.
    async with session_factory() as session:
        async with session.begin():
            worker = worker_factory(session=session, settings=settings, lease_owner=lease_owner)
            job_id = await asyncio.wait_for(worker.run_once(), timeout=settings.job_timeout_seconds)
            if job_id is not None and not isinstance(job_id, UUID):
                raise ValueError('Invitation worker returned an invalid job receipt.')
        return job_id

async def run_invitation_worker(*, session_factory, settings, stop, once=False, lease_owner=None,
    worker_factory=build_invitation_runtime_worker):
    owner = lease_owner or ('invitation-email:' + str(uuid4()))
    processed = 0
    while not stop.is_set():
        job_id = await process_invitation_once(session_factory=session_factory, settings=settings,
            lease_owner=owner, worker_factory=worker_factory)
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
