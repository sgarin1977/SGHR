from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class ApiSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )


class ResponseMeta(ApiSchema):
    pass


class HealthData(ApiSchema):
    status: Literal["ok"]
    service: Literal["sghr-api"]
    api_version: Literal["v1"]


class HealthResponse(ApiSchema):
    data: HealthData
    meta: ResponseMeta
    request_id: str


class MeData(ApiSchema):
    id: UUID
    tenant_id: UUID
    active_role: str | None
    roles: tuple[str, ...]
    language_code: str
    timezone: str | None
    status: str


class MeResponse(ApiSchema):
    data: MeData
    meta: ResponseMeta
    request_id: str


class RolesData(ApiSchema):
    active_role: str | None
    roles: tuple[str, ...]


class RolesResponse(ApiSchema):
    data: RolesData
    meta: ResponseMeta
    request_id: str


class RoleSwitchRequest(ApiSchema):
    role: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


class RoleSwitchResponse(ApiSchema):
    data: RolesData
    meta: ResponseMeta
    request_id: str


class MeUpdateRequest(ApiSchema):
    language_code: str = Field(
        min_length=2,
        max_length=10,
    )


class SpecialistCabinetItem(ApiSchema):
    id: UUID
    profession_name: str
    moderation_status: str
    availability_status: str
    is_selected: bool


class SpecialistCabinetListData(ApiSchema):
    items: tuple[
        SpecialistCabinetItem,
        ...,
    ]


class SpecialistCabinetListResponse(ApiSchema):
    data: SpecialistCabinetListData
    meta: ResponseMeta
    request_id: str


class SpecialistCabinetCreateRequest(ApiSchema):
    category_id: UUID
    profession_id: UUID


class SpecialistCabinetData(ApiSchema):
    item: SpecialistCabinetItem


class SpecialistCabinetResponse(ApiSchema):
    data: SpecialistCabinetData
    meta: ResponseMeta
    request_id: str


class SpecialistCabinetUpdateRequest(ApiSchema):
    is_selected: Literal[True]


class SpecialistCabinetSelectionData(ApiSchema):
    id: UUID
    is_selected: Literal[True]
    changed: bool


class SpecialistCabinetSelectionResponse(
    ApiSchema
):
    data: SpecialistCabinetSelectionData
    meta: ResponseMeta
    request_id: str


class TelegramAuthRequest(ApiSchema):
    init_data: str = Field(
        min_length=1,
        max_length=16384,
    )
    device_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    device_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )


class TokenPairData(ApiSchema):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"]
    expires_in: int = Field(gt=0)


class TelegramAuthResponse(ApiSchema):
    data: TokenPairData
    meta: ResponseMeta
    request_id: str


class RefreshTokenRequest(ApiSchema):
    refresh_token: str = Field(
        min_length=1,
        max_length=512,
    )


class RefreshTokenResponse(ApiSchema):
    data: TokenPairData
    meta: ResponseMeta
    request_id: str


class LogoutData(ApiSchema):
    logged_out: Literal[True]


class LogoutResponse(ApiSchema):
    data: LogoutData
    meta: ResponseMeta
    request_id: str


class AuthSessionItem(ApiSchema):
    id: UUID
    auth_method: str
    device_id: str | None
    device_name: str | None
    status: str
    expires_at: datetime
    last_used_at: datetime | None
    created_at: datetime


class AuthSessionListData(ApiSchema):
    items: tuple[AuthSessionItem, ...]


class AuthSessionListResponse(ApiSchema):
    data: AuthSessionListData
    meta: ResponseMeta
    request_id: str



class EmailAuthChallengeRequest(ApiSchema):
    email: str = Field(
        min_length=3,
        max_length=320,
    )
    challenge_type: Literal[
        "otp",
        "magic_link",
    ]
    device_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    device_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )


class EmailAuthChallengeData(ApiSchema):
    challenge_id: UUID
    challenge_type: Literal[
        "otp",
        "magic_link",
    ]
    expires_at: datetime


class EmailAuthChallengeResponse(ApiSchema):
    data: EmailAuthChallengeData
    meta: ResponseMeta
    request_id: str



class EmailAuthVerifyRequest(ApiSchema):
    challenge_id: UUID
    email: str = Field(
        min_length=3,
        max_length=320,
    )
    secret: str = Field(
        min_length=1,
        max_length=512,
    )
    language_code: str = Field(
        min_length=2,
        max_length=10,
    )


class EmailAuthVerifyResponse(ApiSchema):
    data: TokenPairData
    meta: ResponseMeta
    request_id: str



class EmailMagicLinkVerifyRequest(ApiSchema):
    token: str = Field(
        min_length=1,
        max_length=1024,
    )
    language_code: str = Field(
        min_length=2,
        max_length=10,
    )



class LogoutAllData(ApiSchema):
    logged_out: Literal[True]
    revoked_sessions: int = Field(ge=0)


class LogoutAllResponse(ApiSchema):
    data: LogoutAllData
    meta: ResponseMeta
    request_id: str


class SpecialistServiceItem(ApiSchema):
    id: UUID
    title: str
    description: str | None
    price_from: float | None
    price_to: float | None
    currency: str
    price_unit: str
    status: str


class SpecialistServiceListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class SpecialistServiceListResponse(ApiSchema):
    data: tuple[
        SpecialistServiceItem,
        ...,
    ]
    meta: SpecialistServiceListMeta
    request_id: str


class SpecialistPortfolioItem(ApiSchema):
    id: UUID
    title: str | None
    description: str | None
    file_type: str
    mime_type: str | None
    size_bytes: int | None
    url: str | None
    status: str
    created_at: datetime


class SpecialistPortfolioListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class SpecialistPortfolioListResponse(ApiSchema):
    data: tuple[
        SpecialistPortfolioItem,
        ...,
    ]
    meta: SpecialistPortfolioListMeta
    request_id: str


class SpecialistReviewItem(ApiSchema):
    id: UUID
    rating: int = Field(ge=1, le=5)
    text: str | None
    specialist_reply: str | None
    created_at: datetime


class SpecialistReviewReputation(ApiSchema):
    score: float
    review_count: int = Field(ge=0)


class SpecialistReviewListData(ApiSchema):
    items: tuple[
        SpecialistReviewItem,
        ...,
    ]
    reputation: (
        SpecialistReviewReputation | None
    )


class SpecialistReviewListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class SpecialistReviewListResponse(ApiSchema):
    data: SpecialistReviewListData
    meta: SpecialistReviewListMeta
    request_id: str



class SpecialistSkillItem(ApiSchema):
    id: UUID
    name: str
    is_selected: bool


class SpecialistSkillListData(ApiSchema):
    items: tuple[SpecialistSkillItem, ...]


class SpecialistSkillListResponse(ApiSchema):
    data: SpecialistSkillListData
    meta: ResponseMeta
    request_id: str



class SpecialistSkillsUpdateRequest(ApiSchema):
    skill_ids: list[UUID] = Field(
        max_length=100,
    )


class SpecialistSkillsUpdateData(ApiSchema):
    selected_ids: tuple[UUID, ...]
    changed: bool


class SpecialistSkillsUpdateResponse(ApiSchema):
    data: SpecialistSkillsUpdateData
    meta: ResponseMeta
    request_id: str



class SpecialistAvailabilityData(ApiSchema):
    status: Literal[
        "available",
        "busy",
        "vacation",
        "temporarily_unavailable",
    ]


class SpecialistAvailabilityResponse(ApiSchema):
    data: SpecialistAvailabilityData
    meta: ResponseMeta
    request_id: str



class SpecialistAvailabilityUpdateRequest(ApiSchema):
    status: Literal[
        "available",
        "busy",
        "vacation",
        "temporarily_unavailable",
    ]


class SpecialistAvailabilityUpdateData(ApiSchema):
    status: Literal[
        "available",
        "busy",
        "vacation",
        "temporarily_unavailable",
    ]
    changed: bool


class SpecialistAvailabilityUpdateResponse(
    ApiSchema
):
    data: SpecialistAvailabilityUpdateData
    meta: ResponseMeta
    request_id: str



class SpecialistOrderItem(ApiSchema):
    id: UUID
    thread_id: UUID
    contact_request_id: UUID | None
    client_name: str
    profession_name: str | None
    status: str
    description: str | None
    schedule_text: str | None
    agreed_amount: float | None
    currency: str
    created_at: datetime


class SpecialistOrderListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class SpecialistOrderListResponse(ApiSchema):
    data: tuple[SpecialistOrderItem, ...]
    meta: SpecialistOrderListMeta
    request_id: str


class SpecialistClientItem(ApiSchema):
    id: UUID
    display_name: str
    requests_count: int
    first_request_at: datetime
    last_request_at: datetime
    last_request_status: str
    latest_thread_id: UUID | None


class SpecialistClientListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class SpecialistClientListResponse(ApiSchema):
    data: tuple[SpecialistClientItem, ...]
    meta: SpecialistClientListMeta
    request_id: str


class SpecialistPromotionItem(ApiSchema):
    id: UUID
    promotion_type: str
    starts_at: datetime | None
    ends_at: datetime | None
    price: Decimal
    currency: str
    status: str
    created_at: datetime


class SpecialistPromotionListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class SpecialistPromotionListResponse(
    ApiSchema
):
    data: tuple[
        SpecialistPromotionItem,
        ...,
    ]
    meta: SpecialistPromotionListMeta
    request_id: str


class SpecialistSubscriptionItem(ApiSchema):
    id: UUID
    plan_code: str
    status: str
    billing_period: str
    amount: Decimal
    currency: str
    starts_at: datetime
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    cancelled_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SpecialistSubscriptionListMeta(
    ApiSchema
):
    next_cursor: str | None
    has_more: bool


class SpecialistSubscriptionListResponse(
    ApiSchema
):
    data: tuple[
        SpecialistSubscriptionItem,
        ...,
    ]
    meta: SpecialistSubscriptionListMeta
    request_id: str


class SpecialistCalendarWorkIntervalItem(
    ApiSchema
):
    id: UUID
    weekday: int = Field(ge=1, le=7)
    start_time: time
    end_time: time


class SpecialistCalendarExceptionItem(
    ApiSchema
):
    id: UUID
    exception_date: date
    exception_type: Literal[
        "available",
        "unavailable",
    ]
    start_time: time | None
    end_time: time | None
    reason: str | None


class SpecialistCalendarExceptionCreateRequest(
    ApiSchema
):
    exception_date: date
    exception_type: Literal[
        "available",
        "unavailable",
    ]
    start_time: time | None = None
    end_time: time | None = None
    reason: str | None = Field(
        default=None,
        max_length=500,
    )


class SpecialistCalendarExceptionResponse(
    ApiSchema
):
    data: SpecialistCalendarExceptionItem
    meta: ResponseMeta
    request_id: str


class SpecialistCalendarExceptionDeleteData(
    ApiSchema
):
    id: UUID
    deleted: Literal[True]


class SpecialistCalendarExceptionDeleteResponse(
    ApiSchema
):
    data: SpecialistCalendarExceptionDeleteData
    meta: ResponseMeta
    request_id: str


class SpecialistCalendarExceptionListResponse(
    ApiSchema
):
    data: tuple[
        SpecialistCalendarExceptionItem,
        ...,
    ]
    meta: ResponseMeta
    request_id: str


class SpecialistCalendarSlotItem(ApiSchema):
    start_at: datetime
    end_at: datetime


class SpecialistCalendarSlotData(ApiSchema):
    timezone: str
    items: tuple[
        SpecialistCalendarSlotItem,
        ...,
    ]


class SpecialistCalendarSlotListResponse(
    ApiSchema
):
    data: SpecialistCalendarSlotData
    meta: ResponseMeta
    request_id: str


class SpecialistCalendarScheduleIntervalRequest(
    ApiSchema
):
    weekday: int = Field(ge=1, le=7)
    start_time: time
    end_time: time


class SpecialistCalendarScheduleUpdateRequest(
    ApiSchema
):
    timezone: str = Field(
        min_length=1,
        max_length=255,
    )
    slot_duration_minutes: int = Field(
        gt=0,
        le=1440,
    )
    work_intervals: tuple[
        SpecialistCalendarScheduleIntervalRequest,
        ...,
    ] = Field(
        max_length=100,
    )


class SpecialistCalendarData(ApiSchema):
    professional_cabinet_id: UUID
    timezone: str
    slot_duration_minutes: int = Field(
        gt=0,
    )
    is_active: bool
    work_intervals: tuple[
        SpecialistCalendarWorkIntervalItem,
        ...,
    ]


class SpecialistCalendarResponse(ApiSchema):
    data: SpecialistCalendarData
    meta: ResponseMeta
    request_id: str


class HrVacancyItem(ApiSchema):
    id: UUID
    profession_id: UUID | None
    country_id: UUID | None
    city_id: UUID | None
    title: str
    description: str | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    currency: str
    employment_type: str | None
    work_format: str | None
    recruitment_mode: str
    status: str
    published_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class HrVacancyListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class HrVacancyListResponse(ApiSchema):
    data: tuple[HrVacancyItem, ...]
    meta: HrVacancyListMeta
    request_id: str




class HrApplicationItem(ApiSchema):
    id: UUID
    vacancy_id: UUID
    message: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class HrApplicationListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class HrApplicationListResponse(ApiSchema):
    data: tuple[HrApplicationItem, ...]
    meta: HrApplicationListMeta
    request_id: str


class SpecialistStatisticsData(ApiSchema):
    professional_cabinet_id: UUID
    period: Literal[
        "7d",
        "30d",
        "90d",
    ] | None
    date_from: date
    date_to: date
    timezone: str
    requests: int = Field(ge=0)
    unique_clients: int = Field(ge=0)
    started_dialogs: int = Field(ge=0)
    completed_dialogs: int = Field(ge=0)
    orders: int = Field(ge=0)
    completed_orders: int = Field(ge=0)
    published_reviews: int = Field(ge=0)
    average_published_rating: (
        float | None
    ) = Field(default=None, ge=0, le=5)
    request_to_dialog: float = Field(ge=0)
    dialog_to_order: float = Field(ge=0)
    order_to_completed: float = Field(ge=0)


class SpecialistStatisticsResponse(ApiSchema):
    data: SpecialistStatisticsData
    meta: ResponseMeta
    request_id: str


class SpecialistSearchItem(ApiSchema):
    specialist_id: UUID
    professional_cabinet_id: UUID
    display_name: str
    title: str | None
    short_description: str
    experience_years: int | None
    category_id: UUID
    category_name: str | None
    profession_id: UUID
    profession_name: str | None
    country_id: UUID | None
    city_id: UUID | None
    city_name: str | None
    work_format: str
    languages: tuple[str, ...]
    rating: float = Field(ge=0, le=5)
    reviews_count: int = Field(ge=0)
    is_verified: bool
    is_available: bool
    is_premium: bool
    distance_km: float | None = Field(
        default=None,
        ge=0,
    )
    is_favorite: bool


class SpecialistSearchListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool
    total_count: int = Field(ge=0)


class SpecialistSearchResponse(ApiSchema):
    data: tuple[
        SpecialistSearchItem,
        ...,
    ]
    meta: SpecialistSearchListMeta
    request_id: str


class ContactRequestCreateRequest(ApiSchema):
    specialist_id: UUID
    profession_id: UUID | None = None
    message: str = Field(
        min_length=1,
        max_length=4000,
    )


class ContactRequestCreatedData(ApiSchema):
    contact_request_id: UUID
    thread_id: UUID
    was_existing: bool
    message_masked: bool
    thread_restricted: bool


class ContactRequestCreatedResponse(ApiSchema):
    data: ContactRequestCreatedData
    meta: ResponseMeta
    request_id: str


class ContactRequestListItem(ApiSchema):
    id: UUID
    thread_id: UUID | None
    specialist_name: str
    profession_name: str | None
    message: str
    status: str
    created_at: datetime


class ContactRequestListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class ContactRequestListResponse(ApiSchema):
    data: tuple[
        ContactRequestListItem,
        ...,
    ]
    meta: ContactRequestListMeta
    request_id: str


class ContactRequestDetailResponse(ApiSchema):
    data: ContactRequestListItem
    meta: ResponseMeta
    request_id: str


class ContactRequestCancelData(ApiSchema):
    id: UUID
    thread_id: UUID
    status: str
    thread_status: str


class ContactRequestCancelResponse(ApiSchema):
    data: ContactRequestCancelData
    meta: ResponseMeta
    request_id: str


class DialogListItem(ApiSchema):
    id: UUID
    counterparty_name: str
    profession_name: str | None
    last_message_text: str | None
    last_message_at: datetime | None
    unread_count: int = Field(ge=0)
    status: str


class DialogListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool
    unread_messages: int = Field(ge=0)


class DialogListResponse(ApiSchema):
    data: tuple[DialogListItem, ...]
    meta: DialogListMeta
    request_id: str




class DialogMessageItem(ApiSchema):
    text: str
    original_text: str
    is_sent_by_viewer: bool
    is_system: bool
    created_at: datetime
    used_translation: bool
    attachment: dict | None = None


class DialogDetailData(ApiSchema):
    id: UUID
    contact_request_id: UUID | None
    counterparty_name: str
    profession_name: str | None
    request_text: str | None
    request_status: str | None
    status: str
    active_order_id: UUID | None
    active_order_status: str | None
    show_original_button: bool
    messages: tuple[DialogMessageItem, ...]


class DialogDetailResponse(ApiSchema):
    data: DialogDetailData
    meta: ResponseMeta
    request_id: str



class DialogMessageCreateRequest(ApiSchema):
    text: str | None = Field(
        default=None,
        max_length=4000,
    )
    attachment: dict | None = None


class DialogMessageCreatedData(ApiSchema):
    id: UUID
    dialog_id: UUID
    status: str
    message_masked: bool
    thread_restricted: bool


class DialogMessageCreatedResponse(ApiSchema):
    data: DialogMessageCreatedData
    meta: ResponseMeta
    request_id: str



class DialogFinishData(ApiSchema):
    id: UUID
    action: Literal[
        "requested",
        "pending",
        "completed",
    ]
    contact_request_id: UUID | None
    requested_for_role: Literal[
        "client",
        "specialist",
    ] | None


class DialogFinishResponse(ApiSchema):
    data: DialogFinishData
    meta: ResponseMeta
    request_id: str



class UserOrderCreateRequest(ApiSchema):
    dialog_id: UUID
    description: str | None = Field(
        default=None,
        max_length=4000,
    )
    start_at: datetime | None = None
    end_at: datetime | None = None
    agreed_amount: float | None = Field(
        default=None,
        ge=0,
    )
    currency: str = Field(
        default="EUR",
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )


class UserOrderUpdateRequest(ApiSchema):
    description: str | None = Field(
        max_length=4000,
    )
    start_at: datetime | None
    end_at: datetime | None
    agreed_amount: float | None = Field(
        ge=0,
    )
    currency: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )


class UserOrderCreatedData(ApiSchema):
    id: UUID
    dialog_id: UUID
    contact_request_id: UUID | None
    status: Literal["draft"]


class UserOrderCreatedResponse(ApiSchema):
    data: UserOrderCreatedData
    meta: ResponseMeta
    request_id: str


class UserOrderActionData(ApiSchema):
    id: UUID
    dialog_id: UUID
    contact_request_id: UUID | None
    status: Literal[
        "draft",
        "confirmed",
        "cancelled",
        "completed",
    ]


class UserOrderActionResponse(ApiSchema):
    data: UserOrderActionData
    meta: ResponseMeta
    request_id: str


class UserOrderListItem(ApiSchema):
    id: UUID
    dialog_id: UUID
    contact_request_id: UUID | None
    counterparty_name: str
    profession_name: str | None
    status: str
    description: str | None
    schedule_text: str | None
    agreed_amount: float | None
    currency: str
    created_at: datetime
    actor_role: Literal[
        "client",
        "specialist",
    ]


class UserOrderListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class UserOrderListResponse(ApiSchema):
    data: tuple[UserOrderListItem, ...]
    meta: UserOrderListMeta
    request_id: str


class FavoriteListItem(ApiSchema):
    specialist_id: UUID
    professional_cabinet_id: UUID
    display_name: str
    short_description: str
    city_id: UUID | None
    city_name: str | None
    category_name: str | None
    profession_name: str | None
    work_format: str | None
    services: tuple[str, ...]
    skills: tuple[str, ...]
    languages: tuple[str, ...]
    rating: float = Field(ge=0, le=5)
    reviews_count: int = Field(ge=0)
    is_verified: bool
    is_available: bool
    is_premium: bool


class FavoriteListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class FavoriteListResponse(ApiSchema):
    data: tuple[FavoriteListItem, ...]
    meta: FavoriteListMeta
    request_id: str


class FavoriteSaveRequest(ApiSchema):
    professional_cabinet_id: UUID


class FavoriteSaveData(ApiSchema):
    professional_cabinet_id: UUID
    is_favorite: Literal[True]
    created: bool


class FavoriteSaveResponse(ApiSchema):
    data: FavoriteSaveData
    meta: ResponseMeta
    request_id: str


class ReviewCreateRequest(ApiSchema):
    context_type: Literal[
        "contact_request",
        "service_order",
    ]
    context_id: UUID
    rating: int = Field(ge=1, le=5)
    text: str | None = Field(
        default=None,
        max_length=1000,
    )


class ReviewCreatedData(ApiSchema):
    id: UUID
    context_type: Literal[
        "contact_request",
        "service_order",
    ]
    context_id: UUID
    rating: int = Field(ge=1, le=5)
    text: str | None
    status: str
    created_at: datetime


class ReviewCreatedResponse(ApiSchema):
    data: ReviewCreatedData
    meta: ResponseMeta
    request_id: str


class WhiteLabelBrandingData(ApiSchema):
    logo_url: str | None
    favicon_url: str | None
    primary_color: str | None
    secondary_color: str | None
    accent_color: str | None
    theme_config: dict[str, object]


class WhiteLabelLanguageData(ApiSchema):
    code: str
    name: str
    native_name: str | None


class WhiteLabelLegalDocumentData(ApiSchema):
    doc_type: str
    version: str
    language: str
    title: str | None
    content_url: str | None


class WhiteLabelPricingData(ApiSchema):
    code: str
    name: str
    description: str | None
    price: Decimal
    currency: str


class WhiteLabelConfigData(ApiSchema):
    tenant_slug: str
    tenant_name: str
    default_language: str
    default_currency: str
    branding: WhiteLabelBrandingData
    languages: tuple[
        WhiteLabelLanguageData,
        ...,
    ]
    suites: tuple[str, ...]
    modules: tuple[str, ...]
    legal_documents: tuple[
        WhiteLabelLegalDocumentData,
        ...,
    ]
    pricing: tuple[
        WhiteLabelPricingData,
        ...,
    ]


class WhiteLabelConfigResponse(ApiSchema):
    data: WhiteLabelConfigData
    meta: ResponseMeta
    request_id: str




class AdminUserListItem(ApiSchema):
    id: UUID
    active_role: str | None
    language_code: str
    country_id: UUID | None
    city_id: UUID | None
    status: str
    last_seen_at: datetime | None
    created_at: datetime


class AdminUserListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class AdminUserListResponse(ApiSchema):
    data: tuple[AdminUserListItem, ...]
    meta: AdminUserListMeta
    request_id: str



class AdminUserDetailResponse(ApiSchema):
    data: AdminUserListItem
    meta: ResponseMeta
    request_id: str



class AdminSpecialistListItem(ApiSchema):
    id: UUID
    user_id: UUID
    professional_cabinet_id: UUID | None
    category_id: UUID
    profession_id: UUID
    country_id: UUID | None
    city_id: UUID | None
    display_name: str
    status: str
    moderation_status: str | None
    is_verified: bool
    availability_status: str | None
    rating: float
    reviews_count: int
    created_at: datetime


class AdminSpecialistListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class AdminSpecialistListResponse(ApiSchema):
    data: tuple[
        AdminSpecialistListItem,
        ...,
    ]
    meta: AdminSpecialistListMeta
    request_id: str



class AdminSpecialistDetailResponse(ApiSchema):
    data: AdminSpecialistListItem
    meta: ResponseMeta
    request_id: str



class AdminSpecialistModerationRequest(
    ApiSchema
):
    reason: str = Field(
        min_length=1,
        max_length=1000,
    )


class AdminSpecialistModerationData(
    ApiSchema
):
    entity_id: UUID
    status: str
    message: str


class AdminSpecialistModerationResponse(
    ApiSchema
):
    data: AdminSpecialistModerationData
    meta: ResponseMeta
    request_id: str



class AdminReviewListItem(ApiSchema):
    id: UUID
    reviewer_user_id: UUID
    professional_cabinet_id: UUID
    specialist_id: UUID
    service_order_id: UUID | None
    context_type: str | None
    context_id: UUID | None
    rating: int
    text: str | None
    status: str
    published_at: datetime | None
    created_at: datetime


class AdminReviewListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class AdminReviewListResponse(ApiSchema):
    data: tuple[AdminReviewListItem, ...]
    meta: AdminReviewListMeta
    request_id: str



class AdminReviewModerationRequest(ApiSchema):
    reason: str = Field(
        min_length=1,
        max_length=1000,
    )


class AdminReviewModerationData(ApiSchema):
    review_id: UUID
    status: str


class AdminReviewModerationResponse(ApiSchema):
    data: AdminReviewModerationData
    meta: ResponseMeta
    request_id: str




class AdminComplaintListItem(ApiSchema):
    id: UUID
    reporter_label: str
    target_label: str
    reason: str
    status: str
    created_at: datetime
    is_assigned: bool
    has_conversation_context: bool
    requires_admin_escalation: bool


class AdminComplaintListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class AdminComplaintListResponse(ApiSchema):
    data: tuple[AdminComplaintListItem, ...]
    meta: AdminComplaintListMeta
    request_id: str



class AdminComplaintDetailItem(ApiSchema):
    id: UUID
    reporter_label: str
    target_type: str
    target_label: str
    reason: str
    comment: str | None
    status: str
    created_at: datetime
    has_conversation_context: bool
    requires_admin_escalation: bool
    history: tuple[str, ...]


class AdminComplaintDetailResponse(ApiSchema):
    data: AdminComplaintDetailItem
    meta: ResponseMeta
    request_id: str



class AdminComplaintModerationRequest(ApiSchema):
    reason: str = Field(
        min_length=1,
        max_length=1000,
    )


class AdminComplaintModerationData(ApiSchema):
    complaint_id: UUID
    status: str
    message: str


class AdminComplaintModerationResponse(ApiSchema):
    data: AdminComplaintModerationData
    meta: ResponseMeta
    request_id: str



class AdminAuditListItem(ApiSchema):
    id: UUID
    date: str
    actor: str
    action: str
    target: str
    target_type: str
    reason: str
    source: str


class AdminAuditListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class AdminAuditListResponse(ApiSchema):
    data: tuple[AdminAuditListItem, ...]
    meta: AdminAuditListMeta
    request_id: str


class PartnerApiClientCreateRequest(ApiSchema):
    owner_type: Literal[
        "agency",
        "partner",
        "enterprise",
        "service_account",
    ]
    owner_id: UUID | None = None
    name: str = Field(
        min_length=1,
        max_length=200,
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
    )


class PartnerApiClientUpdateRequest(ApiSchema):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    status: Literal[
        "active",
        "suspended",
        "disabled",
    ] | None = None
    metadata: dict[str, object] | None = None


class PartnerApiClientData(ApiSchema):
    id: UUID
    owner_type: str
    owner_id: UUID | None
    name: str
    status: Literal[
        "active",
        "suspended",
        "disabled",
    ]
    metadata: dict[str, object]
    created_at: datetime
    updated_at: datetime


class PartnerApiClientResponse(ApiSchema):
    data: PartnerApiClientData
    meta: ResponseMeta
    request_id: str



class PartnerApiClientListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class PartnerApiClientListResponse(ApiSchema):
    data: tuple[PartnerApiClientData, ...]
    meta: PartnerApiClientListMeta
    request_id: str



class PartnerApiKeyCreateRequest(ApiSchema):
    name: str = Field(
        min_length=1,
        max_length=200,
    )
    expires_at: datetime | None = None
    ip_allowlist: list[str] = Field(
        default_factory=list,
        max_length=100,
    )
    scopes: tuple[str, ...] = Field(
        min_length=1,
        max_length=12,
    )


class PartnerApiKeyIssuedData(ApiSchema):
    id: UUID
    api_client_id: UUID
    name: str
    key_prefix: str
    key: str
    environment: Literal[
        "sandbox",
        "production",
    ]
    status: Literal[
        "active",
        "revoked",
        "expired",
    ]
    expires_at: datetime | None
    last_used_at: datetime | None
    ip_allowlist: tuple[str, ...]
    scopes: tuple[str, ...]
    created_at: datetime


class PartnerApiKeyIssuedResponse(ApiSchema):
    data: PartnerApiKeyIssuedData
    meta: ResponseMeta
    request_id: str



class PartnerApiKeyData(ApiSchema):
    id: UUID
    api_client_id: UUID
    name: str
    key_prefix: str
    environment: Literal[
        "sandbox",
        "production",
    ]
    status: Literal[
        "active",
        "revoked",
        "expired",
    ]
    expires_at: datetime | None
    last_used_at: datetime | None
    ip_allowlist: tuple[str, ...]
    scopes: tuple[str, ...]
    created_at: datetime


class PartnerApiKeyListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class PartnerApiKeyListResponse(ApiSchema):
    data: tuple[PartnerApiKeyData, ...]
    meta: PartnerApiKeyListMeta
    request_id: str



class PartnerApiKeyRevokeData(ApiSchema):
    api_key_id: UUID
    status: Literal["revoked"]


class PartnerApiKeyRevokeResponse(ApiSchema):
    data: PartnerApiKeyRevokeData
    meta: ResponseMeta
    request_id: str



class WebhookEndpointData(ApiSchema):
    id: UUID
    api_client_id: UUID
    callback_url: str
    status: str
    events: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


class WebhookEndpointListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class WebhookEndpointListResponse(ApiSchema):
    data: tuple[WebhookEndpointData, ...]
    meta: WebhookEndpointListMeta
    request_id: str



class WebhookEndpointDetailResponse(ApiSchema):
    data: WebhookEndpointData
    meta: ResponseMeta
    request_id: str

class WebhookEndpointCreateRequest(ApiSchema):
    api_client_id: UUID | None = None
    callback_url: str = Field(
        min_length=1,
        max_length=2048,
    )
    events: tuple[str, ...] = Field(
        min_length=1,
        max_length=10,
    )


class WebhookEndpointUpdateRequest(ApiSchema):
    callback_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    events: tuple[str, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=10,
    )
    status: Literal[
        "active",
        "suspended",
        "disabled",
    ] | None = None


class WebhookEndpointCreatedData(ApiSchema):
    id: UUID
    api_client_id: UUID
    callback_url: str
    status: str
    events: tuple[str, ...]
    created_at: datetime
    secret: str


class WebhookEndpointCreateResponse(ApiSchema):
    data: WebhookEndpointCreatedData
    meta: ResponseMeta
    request_id: str


class WebhookDeliveryData(ApiSchema):
    id: UUID
    webhook_event_id: UUID
    webhook_endpoint_id: UUID
    status: str
    attempt_count: int
    next_attempt_at: datetime | None
    response_status: int | None
    error_category: str | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WebhookDeliveryListMeta(ApiSchema):
    next_cursor: str | None
    has_more: bool


class WebhookDeliveryListResponse(ApiSchema):
    data: tuple[WebhookDeliveryData, ...]
    meta: WebhookDeliveryListMeta
    request_id: str


class WebhookDeliveryDetailResponse(ApiSchema):
    data: WebhookDeliveryData
    meta: ResponseMeta
    request_id: str




class FileUploadRequest(ApiSchema):
    object_type: Literal[
        "portfolio",
        "message",
        "cv",
        "document",
    ]
    entity_id: UUID | None = None
    filename: str = Field(
        min_length=1,
        max_length=255,
    )
    mime_type: str = Field(
        min_length=1,
        max_length=255,
    )
    size_bytes: int = Field(
        ge=1,
    )


class FileUploadRequestData(ApiSchema):
    id: UUID
    upload_url: str
    expires_in: int
    status: str
    mime_type: str
    size_bytes: int


class FileUploadRequestResponse(ApiSchema):
    data: FileUploadRequestData
    meta: ResponseMeta
    request_id: str



class FileCompleteData(ApiSchema):
    id: UUID
    status: str
    antivirus_status: str
    mime_type: str
    size_bytes: int
    completed_at: datetime


class FileCompleteResponse(ApiSchema):
    data: FileCompleteData
    meta: ResponseMeta
    request_id: str



class FileDownloadData(ApiSchema):
    id: UUID
    download_url: str
    expires_in: int
    mime_type: str
    size_bytes: int


class FileDownloadResponse(ApiSchema):
    data: FileDownloadData
    meta: ResponseMeta
    request_id: str
