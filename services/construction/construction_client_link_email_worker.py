"""Locked operator-link email delivery; never a consent or membership action."""
from dataclasses import dataclass, field
from datetime import timedelta
from html import escape
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import select

from database.models import ApiIdempotencyRecord
from database.repositories.job_queue import JobLeaseLostError
from services.api_identity import ApiActorContext
from services.construction.construction_client_link_email_queue import (
    ConstructionClientLinkEmailQueue, JOB_TYPE, RESOURCE_TYPE,
    parse_email_job_key, utc,
)
from services.construction.construction_client_link_issuance_workflow import (
    ConstructionClientLinkIssuanceWorkflow, ISSUE,
)
from services.construction.construction_client_link_recipient_context import (
    ConstructionClientLinkRecipientContextCodec,
)
from services.construction.construction_grants import ConstructionAccessContextResolver
from services.construction.construction_invitation_platform_email import require_invitation_page_url
from services.construction.construction_jobs import (
    ConstructionJobPermanentError, ConstructionJobRetryError,
)
from services.construction.construction_permissions import require_construction_access

LANGUAGES = ("uk", "ru", "en", "pt", "pl", "de", "nl")

def unavailable():
    return ConstructionJobPermanentError(
        error_code="CLIENT_LINK_EMAIL_UNAVAILABLE",
    )

@dataclass(frozen=True)
class ClientLinkEmailMaterial:
    job_id: UUID
    issuance: object = field(repr=False)
    email: str = field(repr=False)

class ClientLinkEmailRecordRepository:
    def __init__(self, session):
        self.session = session

    async def find_completed(self, *, record_id, tenant_id, user_id):
        if not all(isinstance(value, UUID) for value in (
            record_id, tenant_id, user_id,
        )):
            raise unavailable()
        statement = select(ApiIdempotencyRecord).where(
            ApiIdempotencyRecord.id == record_id,
            ApiIdempotencyRecord.tenant_id == tenant_id,
            ApiIdempotencyRecord.principal_type == "user",
            ApiIdempotencyRecord.principal_id == user_id,
            ApiIdempotencyRecord.operation == ISSUE,
            ApiIdempotencyRecord.status == "completed",
            ApiIdempotencyRecord.response_status == 201,
        ).execution_options(autoflush=False, populate_existing=True)
        # Read without an idempotency row lock: issuance locks that row before
        # the client; the worker must not invert that order while holding a job.
        with self.session.no_autoflush:
            return (await self.session.execute(statement)).scalar_one_or_none()

class ClientLinkEmailLoader:
    def __init__(
        self, *, session, records, clients, requests, access,
        principals, emails, receipt_validator, response_codec,
        token_deriver, lease_owner, clock,
    ):
        required = (
            (records, ("find_completed",)),
            (clients, ("get_state_for_update",)),
            (requests, ("get_request_for_update",)),
            (access, ("has_active_platform_membership", "list_active_grants")),
            (principals, ("get_active_user_id",)),
            (emails, ("find_verified_user_id",)),
        )
        if (
            session is None
            or any(
                getattr(item, "session", None) is not session
                or any(not callable(getattr(item, name, None)) for name in names)
                for item, names in required
            )
            or getattr(receipt_validator, "session", None) is not session
            or not callable(getattr(receipt_validator, "validate_receipt", None))
            or not callable(getattr(response_codec, "decrypt", None))
            or not callable(getattr(token_deriver, "derive", None))
            or not callable(getattr(session, "in_transaction", None))
            or not isinstance(lease_owner, str) or not lease_owner.strip()
            or not callable(clock)
        ):
            raise ValueError("Email loader requires shared-session repositories.")
        self.session = session
        self.records, self.clients, self.requests = records, clients, requests
        self.access, self.principals, self.emails = access, principals, emails
        self.validator, self.codec, self.deriver = (
            receipt_validator, response_codec, token_deriver,
        )
        self.lease_owner, self.clock = lease_owner, clock
        self.resolver = ConstructionAccessContextResolver(
            repository=access, now_provider=clock,
        )

    def _lease(self, job):
        if (
            job.status != "RUNNING"
            or job.lease_owner != self.lease_owner
            or utc(job.lease_expires_at) <= utc(self.clock())
        ):
            raise JobLeaseLostError("Client link email lease is unavailable.")

    async def load(self, job):
        try:
            if (
                self.session.in_transaction() is not True
                or any(not isinstance(getattr(job, name, None), UUID)
                       for name in ("id", "tenant_id", "user_id", "resource_id"))
                or job.job_type != JOB_TYPE
                or job.resource_type != RESOURCE_TYPE
            ):
                raise unavailable()
            self._lease(job)
            record_id, request_id = parse_email_job_key(job.idempotency_key)
            if request_id != job.resource_id:
                raise unavailable()
            record = await self.records.find_completed(
                record_id=record_id, tenant_id=job.tenant_id, user_id=job.user_id,
            )
            if (
                record is None or record.id != record_id
                or record.tenant_id != job.tenant_id
                or record.principal_id != job.user_id
                or record.principal_type != "user" or record.operation != ISSUE
                or record.status != "completed" or record.response_status != 201
            ):
                raise unavailable()
            saved = self.codec.decrypt(record.response_ciphertext)
            binding = {
                "tenant_id": str(job.tenant_id),
                "principal_type": "user", "principal_id": str(job.user_id),
                "operation": ISSUE, "key_hash": record.key_hash,
                "request_hash": record.request_hash,
                "created_at": utc(record.created_at).isoformat(),
            }
            if (
                not isinstance(saved, dict)
                or set(saved) != {"binding", "material", "email_delivery"}
                or saved["binding"] != binding
                or not isinstance(saved["email_delivery"], dict)
            ):
                raise unavailable()
            receipt = saved["email_delivery"]
            command = receipt["command"]
            token = self.deriver.derive(
                reservation_id=record.id, reserved_at=record.created_at,
                tenant_id=job.tenant_id, initiated_by_user_id=job.user_id,
                payload=command,
            )
            material = ConstructionClientLinkIssuanceWorkflow._material(
                saved["material"], token,
            )
            actor = ApiActorContext(
                job.user_id, job.tenant_id, None, (), "en", None, "active",
            )
            if self.validator.validate_receipt(
                actor=actor, material=material,
                reservation=SimpleNamespace(record=record),
                payload=command, receipt=receipt,
            ) != job.id:
                raise unavailable()
            state = material.request
            if state.request_id != job.resource_id:
                raise unavailable()

            # Same client-before-request lock order as issue and confirm.
            client = await self.clients.get_state_for_update(
                tenant_id=state.tenant_id, client_id=state.client_id,
            )
            actual = await self.requests.get_request_for_update(
                tenant_id=state.tenant_id, request_id=state.request_id,
                target_user_id=state.target_user_id, token_hash=state.token_hash,
            )
            # Read time again after waiting for both locks.
            self._lease(job)
            now = utc(self.clock())
            if (
                actual != state
                or not state.issued_at <= now < state.expires_at
                or client is None
                or client.client_id != state.client_id
                or client.tenant_id != state.tenant_id
                or client.row_version != state.expected_client_row_version
                or client.linked_user_id != state.expected_linked_user_id
            ):
                raise unavailable()
            for user_id in (state.initiated_by_user_id, state.target_user_id):
                if await self.principals.get_active_user_id(user_id=user_id) != user_id:
                    raise unavailable()
            if await self.access.has_active_platform_membership(
                tenant_id=state.tenant_id, user_id=state.initiated_by_user_id,
            ) is not True:
                raise unavailable()
            context = await self.resolver.resolve(actor=actor)
            require_construction_access(
                actor=actor, access_context=context,
                permission_code="construction.projects.edit",
            )
            # Never redirect a pending request to a new owner of this email.
            if await self.emails.find_verified_user_id(
                email=command["target_email"],
            ) != state.target_user_id:
                raise unavailable()
            self._lease(job)
            if not state.issued_at <= utc(self.clock()) < state.expires_at:
                raise unavailable()
            return ClientLinkEmailMaterial(
                job_id=job.id, issuance=material, email=command["target_email"],
            )
        except JobLeaseLostError:
            raise
        except ConstructionJobPermanentError:
            raise
        except Exception:
            raise unavailable() from None

MESSAGES = {
    "uk": (
        "SGHR — підтвердження зв’язку з карткою клієнта",
        "Для вашого Platform-акаунта запитано зв’язок із карткою клієнта.",
        "Увійдіть у Platform і підтвердьте зв’язок лише за власною згодою.",
        "Це не додає вас до організації та не надає Construction-доступу.",
        "Переглянути запит", "Діє до",
    ),
    "ru": (
        "SGHR — подтверждение связи с карточкой клиента",
        "Для вашего Platform-аккаунта запрошена связь с карточкой клиента.",
        "Войдите в Platform и подтвердите связь только с вашего согласия.",
        "Это не добавляет вас в организацию и не даёт Construction-доступ.",
        "Посмотреть запрос", "Действует до",
    ),
    "en": (
        "SGHR — confirm a client identity link",
        "A client identity link has been requested for your Platform account.",
        "Sign in to Platform and confirm only if you agree.",
        "This does not join an organization or grant Construction access.",
        "Review request", "Expires at",
    ),
    "pt": (
        "SGHR — confirmar ligação ao cliente",
        "Foi pedida uma ligação de cliente à sua conta Platform.",
        "Inicie sessão na Platform e confirme apenas se concordar.",
        "Isto não adiciona a sua conta à organização nem dá acesso ao Construction.",
        "Consultar pedido", "Expira em",
    ),
    "pl": (
        "SGHR — potwierdź powiązanie klienta",
        "Poproszono o powiązanie karty klienta z Twoim kontem Platform.",
        "Zaloguj się do Platform i potwierdź tylko za swoją zgodą.",
        "Nie dodaje to do organizacji ani nie przyznaje dostępu do Construction.",
        "Zobacz prośbę", "Wygasa",
    ),
    "de": (
        "SGHR — Kundenverknüpfung bestätigen",
        "Eine Kundenverknüpfung mit Ihrem Platform-Konto wurde angefragt.",
        "Melden Sie sich bei Platform an und bestätigen Sie nur, wenn Sie zustimmen.",
        "Dies fügt Sie keiner Organisation hinzu und gewährt keinen Construction-Zugriff.",
        "Anfrage ansehen", "Gültig bis",
    ),
    "nl": (
        "SGHR — bevestig een klantkoppeling",
        "Er is een klantkoppeling met je Platform-account aangevraagd.",
        "Meld je aan bij Platform en bevestig alleen als je akkoord gaat.",
        "Dit voegt je niet toe aan een organisatie en geeft geen Construction-toegang.",
        "Verzoek bekijken", "Geldig tot",
    ),
}

class ClientLinkEmailSender:
    def __init__(
        self, *, provider, from_address, confirmation_page_url,
        encryption_key, clock, language_code="en",
    ):
        from services.construction.construction_client_link_email import normalize_client_link_email
        if not callable(getattr(provider, "send_email", None)) or not callable(clock):
            raise ValueError("Platform provider and clock are required.")
        if language_code not in LANGUAGES:
            raise ValueError("Unsupported email language.")
        self.provider = provider
        self.from_address = normalize_client_link_email(from_address)
        self.page = require_invitation_page_url(confirmation_page_url)
        self.codec = ConstructionClientLinkRecipientContextCodec(
            encryption_key=encryption_key,
        )
        self.clock, self.language = clock, language_code

    async def send(self, *, material, job):
        if (
            not isinstance(material, ClientLinkEmailMaterial)
            or material.job_id != job.id
            or job.job_type != JOB_TYPE or job.resource_type != RESOURCE_TYPE
            or type(job.attempts) is not int or not 1 <= job.attempts <= 3
        ):
            raise unavailable()
        state = material.issuance.request
        if (
            state.request_id != job.resource_id
            or state.tenant_id != job.tenant_id
            or state.initiated_by_user_id != job.user_id
            or not state.issued_at <= utc(self.clock()) < state.expires_at
        ):
            raise unavailable()
        envelope = self.codec.issue_replayable_for_request(
            material=material.issuance,
        )
        link = self.page + "#" + urlencode({
            "recipient_context": envelope.recipient_context,
            "token": envelope.token,
        })
        subject, intro, login, meaning, label, expiry = MESSAGES[self.language]
        deadline = expiry + ": " + state.expires_at.isoformat()
        text_body = "\n\n".join((intro, login, meaning, link, deadline))
        html_body = (
            "<p>" + escape(intro) + "</p><p>" + escape(login)
            + "</p><p>" + escape(meaning) + '</p><p><a href="'
            + escape(link, quote=True) + '">' + escape(label)
            + "</a></p><p>" + escape(deadline) + "</p>"
        )
        try:
            await self.provider.send_email(
                from_address=self.from_address, to_address=material.email,
                subject=subject, text_body=text_body, html_body=html_body,
            )
        except Exception:
            retry_at = utc(self.clock()) + timedelta(seconds=60)
            if job.attempts < 3 and retry_at < state.expires_at:
                raise ConstructionJobRetryError(
                    error_code="CLIENT_LINK_EMAIL_RETRY",
                    available_at=retry_at,
                ) from None
            raise ConstructionJobPermanentError(
                error_code="CLIENT_LINK_EMAIL_FAILED",
            ) from None

class ClientLinkEmailHandler:
    def __init__(self, *, session, loader, sender):
        if (
            getattr(loader, "session", None) is not session
            or not callable(getattr(loader, "load", None))
            or not callable(getattr(sender, "send", None))
        ):
            raise ValueError("Email handler requires shared-session wiring.")
        self.session, self.loader, self.sender = session, loader, sender

    async def __call__(self, job):
        if self.session.in_transaction() is not True:
            raise unavailable()
        material = await self.loader.load(job)
        await self.sender.send(material=material, job=job)
        return RESOURCE_TYPE + ":" + str(material.issuance.request.request_id)

def build_client_link_email_handler(
    *, session, encryption_key, lease_owner, clock, provider,
    from_address, confirmation_page_url, language_code="en",
):
    from database.repositories.api_platform_principal import ApiPlatformPrincipalRepository
    from database.repositories.construction import ConstructionAccessRepository
    from database.repositories.construction.construction_client_link_email import ConstructionClientLinkEmailRepository
    from database.repositories.construction.construction_client_link_requests import ConstructionClientLinkRequestRepository
    from database.repositories.construction.construction_client_user_links import ConstructionClientUserLinkRepository
    from services.api_idempotency import ApiIdempotencyResponseCodec
    from services.construction.construction_client_link_issuance_replay import ConstructionClientLinkTokenDeriver
    from services.construction.construction_jobs import build_construction_job_queue

    deriver = ConstructionClientLinkTokenDeriver(encryption_key=encryption_key)
    validator = ConstructionClientLinkEmailQueue(
        session=session, queue=build_construction_job_queue(session=session),
        token_deriver=deriver, now_provider=clock,
    )
    loader = ClientLinkEmailLoader(
        session=session, records=ClientLinkEmailRecordRepository(session),
        clients=ConstructionClientUserLinkRepository(session),
        requests=ConstructionClientLinkRequestRepository(session),
        access=ConstructionAccessRepository(session),
        principals=ApiPlatformPrincipalRepository(session),
        emails=ConstructionClientLinkEmailRepository(session),
        receipt_validator=validator,
        response_codec=ApiIdempotencyResponseCodec(encryption_key=encryption_key),
        token_deriver=deriver, lease_owner=lease_owner, clock=clock,
    )
    sender = ClientLinkEmailSender(
        provider=provider, from_address=from_address,
        confirmation_page_url=confirmation_page_url,
        encryption_key=encryption_key, clock=clock, language_code=language_code,
    )
    return ClientLinkEmailHandler(session=session, loader=loader, sender=sender)

# client_link_email_worker_v1
