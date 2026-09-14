from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.contact import (
    ContactChatRepository,
)
from database.repositories.webhooks import (
    WebhookRepository,
)
from database.repositories.moderation import (
    ModerationRepository,
)
from database.repositories.specialist import (
    SpecialistRepository,
)
from database.repositories.user import (
    UserRepository,
)
from database.repositories.translation import (
    TranslationRepository,
    normalize_translation_language,
)
from services.api_idempotency import (
    ApiIdempotencyService,
)
from services.contact_chat import (
    ContactChatError,
    ContactChatService,
)
from services.webhooks import (
    WebhookEventPublisher,
)
from services.moderation import (
    ModerationService,
)
from services.specialist import (
    SpecialistService,
)
from services.translation import (
    TranslationService,
)
from services.user import UserService
from services.user_settings import (
    UserSettingsNotFoundError,
    UserSettingsService,
)


class UserDialogsAccessError(
    PermissionError
):
    pass


class UserDialogsThreadError(ValueError):
    pass


class UserDialogsSelectionError(
    ValueError
):
    pass


@dataclass(frozen=True)
class UserDialogsActor:
    user_id: UUID
    tenant_id: UUID
    language: str


@dataclass(frozen=True)
class UserDialogsPage:
    actor: UserDialogsActor
    items: list
    unread_messages: int
    page: int
    has_next: bool
    show_role_switch: bool = False


@dataclass(frozen=True)
class UserContactRequestAction:
    actor: UserDialogsActor
    result: Any


@dataclass(frozen=True)
class UserContactRequestsPage:
    actor: UserDialogsActor
    items: list
    page: int
    has_next: bool


@dataclass(frozen=True)
class UserDialogDetail:
    actor: UserDialogsActor
    detail: object


@dataclass(frozen=True)
class UserContactRequestResult:
    contact_request_id: UUID
    thread_id: UUID
    was_existing: bool
    message_masked: bool
    thread_restricted: bool


@dataclass(frozen=True)
class UserDialogContact:
    actor: UserDialogsActor
    chat: Any


@dataclass(frozen=True)
class UserDialogMessage:
    actor: UserDialogsActor
    result: Any


@dataclass(frozen=True)
class UserDialogNotification:
    actor: UserDialogsActor
    context: Any


@dataclass(frozen=True)
class UserDialogMessageAction:
    actor: UserDialogsActor
    result: Any
    receiver_platform_user_id: Any
    receiver_language: str
    receiver_notification_context: Any | None
    receiver_notification_message: str
    receiver_used_translation: bool
    receiver_translation_status: str


@dataclass(frozen=True)
class UserDialogCompletion:
    actor: UserDialogsActor
    result: Any
    receiver_chat_id: str | None = None
    receiver_language: str = "ru"

@dataclass(frozen=True)
class UserDialogsSearchResult:
    actor: UserDialogsActor
    role: str
    view: str
    items: list
    unread_messages: int
    has_next: bool


@dataclass(frozen=True)
class UserDialogComplaintTarget:
    actor: UserDialogsActor
    target_type: str
    target_id: UUID
    conversation_thread_id: UUID


class UserDialogsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: (
            UserSettingsService | None
        ) = None,
        users: UserService | None = None,
        chats: ContactChatService | None = None,
        specialists: (
            SpecialistService | None
        ) = None,
        user_repository: (
            UserRepository | None
        ) = None,
        moderation: (
            ModerationService | None
        ) = None,
        translation: (
            TranslationService | None
        ) = None,
        idempotency: (
            ApiIdempotencyService | None
        ) = None,
    ):
        self.session = session
        self.idempotency = idempotency
        self.settings = (
            settings
            or UserSettingsService(session)
        )
        self.users = (
            users
            or UserService(session)
        )
        self.chats = (
            chats
            or ContactChatService(
                ContactChatRepository(session),
                webhook_publisher=(
                    WebhookEventPublisher(
                        repository=WebhookRepository(
                            session
                        )
                    )
                ),
            )
        )
        self.specialists = (
            specialists
            or SpecialistService(
                SpecialistRepository(session)
            )
        )
        self.user_repository = (
            user_repository
            or UserRepository(session)
        )
        self.moderation = (
            moderation
            or ModerationService(
                ModerationRepository(session)
            )
        )
        self.translation = (
            translation
            or TranslationService(
                TranslationRepository(session)
            )
        )

    @staticmethod
    def parse_thread_id(
        thread_id: UUID | str,
    ) -> UUID:
        try:
            return UUID(str(thread_id))
        except (TypeError, ValueError) as exc:
            raise UserDialogsThreadError(
                "Invalid dialog thread id."
            ) from exc

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> UserDialogsActor:
        try:
            context = await self.settings.get_context(
                platform_user_id=platform_user_id,
            )
        except UserSettingsNotFoundError as exc:
            raise UserDialogsAccessError(
                "Dialog actor not found."
            ) from exc

        return UserDialogsActor(
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            language=context.interface_language,
        )

    async def list_dialogs_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        language: str,
        role: str,
        view: str = "active",
        page: int = 0,
        page_size: int = 20,
        search_query: str | None = None,
    ) -> UserDialogsPage:
        if role not in {
            "client",
            "specialist",
        }:
            raise UserDialogsSelectionError(
                "Invalid dialog role."
            )

        if view not in {
            "active",
            "new",
            "completed",
            "archive",
            "hidden",
        }:
            raise UserDialogsSelectionError(
                "Invalid dialog view."
            )

        actor = UserDialogsActor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=(
                normalize_translation_language(
                    language
                )
            ),
        )
        normalized_page = max(0, int(page))
        normalized_size = max(
            1,
            int(page_size),
        )
        normalized_query = (
            (search_query or "").strip()
            or None
        )

        list_method = (
            self.chats.list_client_threads
            if role == "client"
            else self.chats.list_specialist_threads
        )

        items = await list_method(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            view=view,
            limit=normalized_size + 1,
            offset=(
                normalized_page
                * normalized_size
            ),
            language=actor.language,
            search_query=normalized_query,
        )

        visible_items = items[
            :normalized_size
        ]

        unread_messages = await (
            self.chats.count_unread_messages(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                participant_role=role,
            )
        )

        await self.chats.record_messages_opened(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            participant_role=role,
            view=view,
            page=normalized_page,
            items_count=len(visible_items),
            platform="api",
        )

        return UserDialogsPage(
            actor=actor,
            items=visible_items,
            unread_messages=unread_messages,
            page=normalized_page,
            has_next=(
                len(items) > normalized_size
            ),
            show_role_switch=False,
        )

    async def get_dialog_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        thread_id: UUID | str,
        language: str,
        role: str,
    ) -> UserDialogDetail:
        if role not in {
            "client",
            "specialist",
        }:
            raise UserDialogsSelectionError(
                "Invalid dialog role."
            )

        actor = UserDialogsActor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=(
                normalize_translation_language(
                    language
                )
            ),
        )
        parsed_thread_id = self.parse_thread_id(
            thread_id
        )

        detail = await (
            self.chats
            .get_thread_detail_for_viewer(
                tenant_id=actor.tenant_id,
                thread_id=parsed_thread_id,
                user_id=actor.user_id,
                participant_role=role,
                language=actor.language,
                platform="api",
            )
        )

        await self.chats.mark_thread_read(
            tenant_id=actor.tenant_id,
            thread_id=parsed_thread_id,
            user_id=actor.user_id,
        )

        return UserDialogDetail(
            actor=actor,
            detail=detail,
        )

    async def send_dialog_message_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        thread_id: UUID | str,
        language: str,
        text: str,
        attachment: dict | None = None,
    ) -> UserDialogMessage:
        actor = UserDialogsActor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=(
                normalize_translation_language(
                    language
                )
            ),
        )
        parsed_thread_id = self.parse_thread_id(
            thread_id
        )

        result = await self.chats.send_thread_message(
            tenant_id=actor.tenant_id,
            thread_id=parsed_thread_id,
            sender_user_id=actor.user_id,
            text=text,
            original_language=actor.language,
            attachment=attachment,
            platform="api",
        )

        return UserDialogMessage(
            actor=actor,
            result=result,
        )

    async def finish_dialog_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        thread_id: UUID | str,
        language: str,
    ) -> UserDialogCompletion:
        actor = UserDialogsActor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=(
                normalize_translation_language(
                    language
                )
            ),
        )
        parsed_thread_id = self.parse_thread_id(
            thread_id
        )

        result = await self.chats.finish_thread(
            tenant_id=actor.tenant_id,
            thread_id=parsed_thread_id,
            actor_user_id=actor.user_id,
            platform="api",
        )

        return UserDialogCompletion(
            actor=actor,
            result=result,
        )

    async def list_specialist_dialogs(
        self,
        *,
        platform_user_id: int | str,
        view: str = "active",
        page: int = 0,
        page_size: int = 5,
        search_query: str | None = None,
    ) -> UserDialogsPage:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        normalized_page = max(0, int(page))
        normalized_size = max(1, int(page_size))

        items = await self.chats.list_specialist_threads(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            view=view,
            limit=normalized_size + 1,
            offset=(
                normalized_page
                * normalized_size
            ),
            language=actor.language,
            search_query=search_query,
        )
        unread_messages = (
            await self.chats
            .count_unread_messages(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                participant_role="specialist",
            )
        )
        await self.chats.record_messages_opened(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            participant_role="specialist",
            view=view,
            page=normalized_page,
        )

        return UserDialogsPage(
            actor=actor,
            items=items[:normalized_size],
            unread_messages=unread_messages,
            page=normalized_page,
            has_next=(
                len(items) > normalized_size
            ),
        )

    async def list_client_dialogs(
        self,
        *,
        platform_user_id: int | str,
        view: str = "active",
        page: int = 0,
        page_size: int = 5,
        search_query: str | None = None,
    ) -> UserDialogsPage:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        normalized_page = max(0, int(page))
        normalized_size = max(1, int(page_size))

        items = await self.chats.list_client_threads(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            view=view,
            limit=normalized_size,
            offset=(
                normalized_page
                * normalized_size
            ),
            language=actor.language,
            search_query=search_query,
        )
        unread_messages = (
            await self.chats
            .count_unread_messages(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                participant_role="client",
            )
        )
        await self.chats.record_messages_opened(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            participant_role="client",
            view=view,
            page=normalized_page,
            items_count=len(items),
        )

        role_context = (
            await self.users
            .get_role_switch_context(
                platform_user_id
            )
        )
        show_role_switch = bool(
            role_context
            and len(
                role_context.available_roles
            ) > 1
        )

        return UserDialogsPage(
            actor=actor,
            items=items,
            unread_messages=unread_messages,
            page=normalized_page,
            has_next=(
                len(items) >= normalized_size
            ),
            show_role_switch=show_role_switch,
        )

    async def get_specialist_dialog(
        self,
        *,
        platform_user_id: int | str,
        thread_id: UUID | str,
    ) -> UserDialogDetail:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        parsed_thread_id = self.parse_thread_id(
            thread_id
        )

        detail = await (
            self.chats
            .get_thread_detail_for_viewer(
                tenant_id=actor.tenant_id,
                thread_id=parsed_thread_id,
                user_id=actor.user_id,
                participant_role="specialist",
                language=actor.language,
            )
        )
        await self.chats.mark_thread_read(
            tenant_id=actor.tenant_id,
            thread_id=parsed_thread_id,
            user_id=actor.user_id,
        )

        return UserDialogDetail(
            actor=actor,
            detail=detail,
        )

    async def get_client_dialog(
        self,
        *,
        platform_user_id: int | str,
        thread_id: UUID | str,
    ) -> UserDialogDetail:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
        )
        parsed_thread_id = self.parse_thread_id(
            thread_id
        )

        detail = await self.chats.get_thread_detail(
            thread_id=parsed_thread_id,
            user_id=actor.user_id,
            language=actor.language,
        )
        await self.chats.mark_thread_read(
            tenant_id=actor.tenant_id,
            thread_id=parsed_thread_id,
            user_id=actor.user_id,
        )

        return UserDialogDetail(
            actor=actor,
            detail=detail,
        )

    async def finish_dialog(
        self,
        *,
        platform_user_id: int | str,
        thread_id: UUID | str,
    ) -> UserDialogCompletion:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_thread_id = self.parse_thread_id(
            thread_id
        )

        result = await self.chats.finish_thread(
            tenant_id=actor.tenant_id,
            thread_id=parsed_thread_id,
            actor_user_id=actor.user_id,
        )

        receiver_chat_id: str | None = None
        receiver_language = "ru"

        receiver_user_id = getattr(
            result,
            "requested_for_user_id",
            None,
        )

        if (
            result.action == "requested"
            and receiver_user_id
        ):
            receiver_account = await (
                self.user_repository
                .get_telegram_account_by_user_id(
                    receiver_user_id
                )
            )
            receiver_language = (
                normalize_translation_language(
                    await self.user_repository
                    .get_language_code(
                        receiver_user_id
                    )
                )
            )

            if receiver_account:
                receiver_chat_id = (
                    receiver_account
                    .platform_user_id
                )

        return UserDialogCompletion(
            actor=actor,
            result=result,
            receiver_chat_id=receiver_chat_id,
            receiver_language=receiver_language,
        )

    async def resolve_complaint_target(
        self,
        *,
        platform_user_id: int | str,
        thread_id: UUID | str,
    ) -> UserDialogComplaintTarget:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_thread_id = self.parse_thread_id(
            thread_id
        )

        (
            target_type,
            target_id,
            conversation_thread_id,
        ) = await (
            self.moderation
            .resolve_thread_complaint_target(
                tenant_id=actor.tenant_id,
                reporter_user_id=actor.user_id,
                thread_id=parsed_thread_id,
            )
        )

        return UserDialogComplaintTarget(
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            conversation_thread_id=(
                conversation_thread_id
            ),
        )

    async def search_dialogs(
        self,
        *,
        platform_user_id: int | str,
        role: str,
        view: str,
        search_query: str,
        page_size: int = 5,
    ) -> UserDialogsSearchResult:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        normalized_role = (
            "client"
            if role == "client"
            else "specialist"
        )
        normalized_view = (
            (view or "active").strip()
            or "active"
        )
        normalized_query = (
            search_query or ""
        ).strip()
        normalized_size = max(
            1,
            int(page_size),
        )

        if normalized_role == "client":
            items = (
                await self.chats
                .list_client_threads(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    view=normalized_view,
                    limit=normalized_size,
                    offset=0,
                    language=actor.language,
                    search_query=normalized_query,
                )
            )
        else:
            items = (
                await self.chats
                .list_specialist_threads(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    view=normalized_view,
                    limit=normalized_size + 1,
                    offset=0,
                    language=actor.language,
                    search_query=normalized_query,
                )
            )

        unread_messages = (
            await self.chats
            .count_unread_messages(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                participant_role=(
                    normalized_role
                ),
            )
        )

        return UserDialogsSearchResult(
            actor=actor,
            role=normalized_role,
            view=normalized_view,
            items=items[:normalized_size],
            unread_messages=unread_messages,
            has_next=(
                normalized_role == "specialist"
                and len(items) > normalized_size
            ),
        )

    async def translate_notification_message(
        self,
        *,
        message_id: UUID,
        receiver_user_id: UUID,
    ) -> Any:
        return await (
            self.translation
            .translate_notification_message(
                message_id=message_id,
                receiver_user_id=(
                    receiver_user_id
                ),
            )
        )

    @staticmethod
    def parse_entity_id(
        value: UUID | str,
        *,
        field: str,
    ) -> UUID:
        try:
            return (
                value
                if isinstance(value, UUID)
                else UUID(str(value))
            )
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            raise (
                UserDialogsSelectionError(
                    f"Invalid {field}."
                )
            ) from exc

    @classmethod
    def parse_optional_entity_id(
        cls,
        value: UUID | str | None,
        *,
        field: str,
    ) -> UUID | None:
        if value is None:
            return None

        return cls.parse_entity_id(
            value,
            field=field,
        )

    async def cancel_contact_request_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        language: str,
        contact_request_id: UUID,
    ) -> UserContactRequestAction:
        actor = UserDialogsActor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=(
                normalize_translation_language(
                    language
                )
            ),
        )

        result = await (
            self.chats.cancel_contact_request(
                tenant_id=actor.tenant_id,
                actor_user_id=actor.user_id,
                contact_request_id=(
                    contact_request_id
                ),
                platform="api",
            )
        )

        return UserContactRequestAction(
            actor=actor,
            result=result,
        )

    async def get_contact_request_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        language: str,
        contact_request_id: UUID,
    ) -> UserDialogDetail:
        actor = UserDialogsActor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=(
                normalize_translation_language(
                    language
                )
            ),
        )

        detail = await (
            self.chats
            .get_client_request_detail(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                language=actor.language,
                contact_request_id=(
                    contact_request_id
                ),
            )
        )

        return UserDialogDetail(
            actor=actor,
            detail=detail,
        )

    async def list_contact_requests_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        page: int = 0,
        page_size: int = 20,
    ) -> UserContactRequestsPage:
        actor = UserDialogsActor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=(
                normalize_translation_language(
                    language
                )
            ),
        )
        normalized_page = max(
            0,
            int(page),
        )
        normalized_size = max(
            1,
            int(page_size),
        )

        items = await (
            self.chats.list_client_requests(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                language=actor.language,
                limit=normalized_size + 1,
                offset=(
                    normalized_page
                    * normalized_size
                ),
            )
        )

        return UserContactRequestsPage(
            actor=actor,
            items=items[:normalized_size],
            page=normalized_page,
            has_next=(
                len(items) > normalized_size
            ),
        )

    async def create_contact_request_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        specialist_id: UUID,
        profession_id: UUID | None,
        message: str,
        idempotency_key: str | None = None,
    ) -> UserDialogContact:
        actor = UserDialogsActor(
            user_id=user_id,
            tenant_id=tenant_id,
            language=(
                normalize_translation_language(
                    language
                )
            ),
        )

        reservation = None
        if (
            idempotency_key is not None
            and self.idempotency is not None
        ):
            reservation = await (
                self.idempotency.reserve(
                    tenant_id=tenant_id,
                    principal_type="user",
                    principal_id=user_id,
                    operation=(
                        "contact_request.create"
                    ),
                    idempotency_key=(
                        idempotency_key
                    ),
                    payload={
                        "specialist_id": str(
                            specialist_id
                        ),
                        "profession_id": (
                            str(profession_id)
                            if profession_id
                            is not None
                            else None
                        ),
                        "message": message,
                    },
                )
            )

            if reservation.is_replay:
                stored = (
                    reservation.response_payload
                )
                return UserDialogContact(
                    actor=actor,
                    chat=UserContactRequestResult(
                        contact_request_id=UUID(
                            stored[
                                "contact_request_id"
                            ]
                        ),
                        thread_id=UUID(
                            stored["thread_id"]
                        ),
                        was_existing=bool(
                            stored["was_existing"]
                        ),
                        message_masked=bool(
                            stored["message_masked"]
                        ),
                        thread_restricted=bool(
                            stored[
                                "thread_restricted"
                            ]
                        ),
                    ),
                )

        create_kwargs = {
            "tenant_id": actor.tenant_id,
            "from_user_id": actor.user_id,
            "specialist_id": specialist_id,
            "profession_id": profession_id,
            "message": message,
            "original_language": actor.language,
            "platform": "api",
        }
        if reservation is not None:
            create_kwargs["commit"] = False

        result = await (
            self.chats.start_contact_chat(
                **create_kwargs
            )
        )

        if reservation is not None:
            await self.idempotency.complete(
                reservation=reservation,
                response_status=201,
                response_payload={
                    "contact_request_id": str(
                        result.contact_request_id
                    ),
                    "thread_id": str(
                        result.thread_id
                    ),
                    "was_existing": bool(
                        result.was_existing
                    ),
                    "message_masked": bool(
                        result.message_masked
                    ),
                    "thread_restricted": bool(
                        result.thread_restricted
                    ),
                },
            )
            await self.idempotency.commit()

        return UserDialogContact(
            actor=actor,
            chat=result,
        )

    async def open_contact(
        self,
        *,
        platform_user_id: int | str,
        specialist_id: UUID | str,
        profession_id: UUID | str | None,
        system_message: str,
        original_language: str,
    ) -> UserDialogContact:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )

        chat = await self.chats.open_contact_chat(
            tenant_id=actor.tenant_id,
            from_user_id=actor.user_id,
            specialist_id=(
                self.parse_entity_id(
                    specialist_id,
                    field="specialist",
                )
            ),
            profession_id=(
                self.parse_optional_entity_id(
                    profession_id,
                    field="profession",
                )
            ),
            system_message=system_message,
            original_language=(
                original_language
            ),
        )

        return UserDialogContact(
            actor=actor,
            chat=chat,
        )

    async def get_contact_chat(
        self,
        *,
        platform_user_id: int | str,
        thread_id: UUID | str,
        viewer_role: str,
    ) -> UserDialogDetail:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_thread_id = (
            self.parse_thread_id(
                thread_id
            )
        )
        normalized_role = (
            "specialist"
            if viewer_role == "specialist"
            else "client"
        )

        detail = await (
            self.chats
            .get_thread_detail_for_viewer(
                tenant_id=actor.tenant_id,
                thread_id=parsed_thread_id,
                user_id=actor.user_id,
                participant_role=(
                    normalized_role
                ),
                language=actor.language,
            )
        )

        await self.chats.mark_thread_read(
            tenant_id=actor.tenant_id,
            thread_id=parsed_thread_id,
            user_id=actor.user_id,
        )

        return UserDialogDetail(
            actor=actor,
            detail=detail,
        )

    async def open_thread_notification(
        self,
        *,
        platform_user_id: int | str,
        thread_id: UUID | str,
    ) -> UserDialogNotification:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_thread_id = (
            self.parse_thread_id(
                thread_id
            )
        )

        context = await (
            self.chats
            .get_thread_notification_context(
                thread_id=parsed_thread_id,
                receiver_user_id=(
                    actor.user_id
                ),
                language=actor.language,
            )
        )

        if context.receiver_role == "specialist":
            await (
                self.specialists
                .switch_active_professional_cabinet(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    specialist_id=(
                        context.specialist_id
                    ),
                    professional_cabinet_id=(
                        context
                        .professional_cabinet_id
                    ),
                )
            )

        await self.users.switch_active_role(
            platform_user_id,
            context.receiver_role,
        )

        return UserDialogNotification(
            actor=actor,
            context=context,
        )

    async def send_contact_message(
        self,
        *,
        platform_user_id: int | str,
        thread_id: UUID | str,
        text: str,
        original_language: str,
        attachment: dict | None = None,
    ) -> UserDialogMessageAction:
        actor = await self.require_actor(
            platform_user_id=(
                platform_user_id
            ),
        )
        parsed_thread_id = (
            self.parse_thread_id(
                thread_id
            )
        )

        result = await (
            self.chats.send_thread_message(
                tenant_id=actor.tenant_id,
                thread_id=parsed_thread_id,
                sender_user_id=actor.user_id,
                text=text,
                original_language=(
                    original_language
                ),
                attachment=attachment,
            )
        )

        delivery_context = await (
            self.users
            .get_telegram_delivery_context(
                user_id=(
                    result.receiver_user_id
                ),
            )
        )

        receiver_language = (
            normalize_translation_language(
                delivery_context.language_code
                or actor.language
            )
        )

        try:
            notification_context = await (
                self.chats
                .get_thread_notification_context(
                    thread_id=result.thread_id,
                    receiver_user_id=(
                        result.receiver_user_id
                    ),
                    language=receiver_language,
                )
            )
        except ContactChatError:
            notification_context = None

        translation = await (
            self.translation
            .translate_notification_message(
                message_id=result.message_id,
                receiver_user_id=(
                    result.receiver_user_id
                ),
            )
        )

        return UserDialogMessageAction(
            actor=actor,
            result=result,
            receiver_platform_user_id=(
                delivery_context.platform_user_id
            ),
            receiver_language=(
                receiver_language
            ),
            receiver_notification_context=(
                notification_context
            ),
            receiver_notification_message=(
                translation.display_text
            ),
            receiver_used_translation=(
                translation.used_translation
            ),
            receiver_translation_status=(
                translation.translation_status
            ),
        )
