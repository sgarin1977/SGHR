import asyncio
import json
import os
import uuid
from datetime import datetime
from decimal import Decimal
import re
from sqlalchemy import text

from database.session import async_session


DEFAULT_LEGAL_VERSION = "beta-0.3"


LANGUAGES = [
    {"code": "ru", "name": "Russian", "native_name": "Русский"},
    {"code": "en", "name": "English", "native_name": "English"},
    {"code": "pt", "name": "Portuguese", "native_name": "Português"},
]

CURRENCIES = [
    {"code": "EUR", "name": "Euro", "symbol": "€"},
]

COUNTRIES = [
    {"code": "PT", "name": "Portugal", "name_ru": "Португалия", "name_en": "Portugal", "name_pt": "Portugal", "default_language": "pt", "default_currency": "EUR", "phone_code": "+351"},
    {"code": "ES", "name": "Spain", "name_ru": "Испания", "name_en": "Spain", "name_pt": "Espanha", "default_language": "es", "default_currency": "EUR", "phone_code": "+34"},
    {"code": "PL", "name": "Poland", "name_ru": "Польша", "name_en": "Poland", "name_pt": "Polônia", "default_language": "pl", "default_currency": "EUR", "phone_code": "+48"},
    {"code": "IT", "name": "Italy", "name_ru": "Италия", "name_en": "Italy", "name_pt": "Itália", "default_language": "it", "default_currency": "EUR", "phone_code": "+39"},
    {"code": "DE", "name": "Germany", "name_ru": "Германия", "name_en": "Germany", "name_pt": "Alemanha", "default_language": "de", "default_currency": "EUR", "phone_code": "+49"},
]

CITIES = [
    {"country_code": "PT", "name": "Lisbon", "name_ru": "Лиссабон", "name_en": "Lisbon", "name_pt": "Lisboa", "latitude": Decimal("38.7222524"), "longitude": Decimal("-9.1393366"), "timezone": "Europe/Lisbon"},
    {"country_code": "PT", "name": "Porto", "name_ru": "Порту", "name_en": "Porto", "name_pt": "Porto", "latitude": Decimal("41.1494512"), "longitude": Decimal("-8.6107884"), "timezone": "Europe/Lisbon"},
    {"country_code": "PT", "name": "Setubal", "name_ru": "Сетубал", "name_en": "Setubal", "name_pt": "Setúbal", "latitude": Decimal("38.5243986"), "longitude": Decimal("-8.8881967"), "timezone": "Europe/Lisbon"},
    {"country_code": "PT", "name": "Palmela", "name_ru": "Палмела", "name_en": "Palmela", "name_pt": "Palmela", "latitude": Decimal("38.5690209"), "longitude": Decimal("-8.9012603"), "timezone": "Europe/Lisbon"},
    {"country_code": "PT", "name": "Cascais", "name_ru": "Кашкайш", "name_en": "Cascais", "name_pt": "Cascais", "latitude": Decimal("38.6967571"), "longitude": Decimal("-9.4207438"), "timezone": "Europe/Lisbon"},
    {"country_code": "PT", "name": "Sintra", "name_ru": "Синтра", "name_en": "Sintra", "name_pt": "Sintra", "latitude": Decimal("38.8028687"), "longitude": Decimal("-9.3816589"), "timezone": "Europe/Lisbon"},
    {"country_code": "ES", "name": "Madrid", "name_ru": "Мадрид", "name_en": "Madrid", "name_pt": "Madrid", "latitude": Decimal("40.4167047"), "longitude": Decimal("-3.7035825"), "timezone": "Europe/Madrid"},
    {"country_code": "ES", "name": "Barcelona", "name_ru": "Барселона", "name_en": "Barcelona", "name_pt": "Barcelona", "latitude": Decimal("41.3828939"), "longitude": Decimal("2.1774322"), "timezone": "Europe/Madrid"},
    {"country_code": "PL", "name": "Warsaw", "name_ru": "Варшава", "name_en": "Warsaw", "name_pt": "Varsóvia", "latitude": Decimal("52.2319581"), "longitude": Decimal("21.0067249"), "timezone": "Europe/Warsaw"},
    {"country_code": "PL", "name": "Krakow", "name_ru": "Краков", "name_en": "Krakow", "name_pt": "Cracóvia", "latitude": Decimal("50.0619474"), "longitude": Decimal("19.9368564"), "timezone": "Europe/Warsaw"},
]

CATEGORIES = [
    ("repair", "Repair", "Ремонт", "Repair", "Reparos"),
    ("construction", "Construction", "Строительство", "Construction", "Construção"),
    ("electrician", "Electrician", "Электрика", "Electrician", "Eletricista"),
    ("plumbing", "Plumbing", "Сантехника", "Plumbing", "Canalização"),
    ("cleaning", "Cleaning", "Уборка", "Cleaning", "Limpeza"),
    ("transport", "Transport", "Транспорт", "Transport", "Transporte"),
    ("tourism", "Tourism", "Туризм", "Tourism", "Turismo"),
    ("guides", "Guides", "Гиды", "Guides", "Guias"),
    ("beauty", "Beauty", "Красота", "Beauty", "Beleza"),
    ("documents_legalization", "Documents legalization", "Легализация документов", "Documents legalization", "Legalização de documentos"),
    ("auto", "Auto", "Авто", "Auto", "Automóvel"),
    ("it_digital", "IT and digital", "IT и digital", "IT and digital", "TI e digital"),
    ("education", "Education", "Образование", "Education", "Educação"),
    ("care", "Care", "Уход", "Care", "Cuidados"),
    ("real_estate", "Real estate", "Недвижимость", "Real estate", "Imobiliário"),
]

PROFESSIONS = {
    "repair": [
        ("home_repair_master", "Home repair master", "Мастер по ремонту", "Home repair master", "Técnico de reparos"),
        ("appliance_repair", "Appliance repair", "Ремонт бытовой техники", "Appliance repair", "Reparação de eletrodomésticos"),
    ],
    "construction": [
        ("builder", "Builder", "Строитель", "Builder", "Construtor"),
        ("tiler", "Tiler", "Плиточник", "Tiler", "Ladrilhador"),
    ],
    "electrician": [
        ("electrician_general", "Electrician", "Электрик", "Electrician", "Eletricista"),
        ("low_voltage_specialist", "Low-voltage specialist", "Слаботочные системы", "Low-voltage specialist", "Baixa tensão"),
    ],
    "plumbing": [
        ("plumber", "Plumber", "Сантехник", "Plumber", "Canalizador"),
        ("heating_specialist", "Heating specialist", "Отопление", "Heating specialist", "Aquecimento"),
    ],
    "cleaning": [
        ("home_cleaner", "Home cleaner", "Клинер", "Home cleaner", "Limpeza doméstica"),
        ("deep_cleaning", "Deep cleaning", "Генеральная уборка", "Deep cleaning", "Limpeza profunda"),
    ],
    "transport": [
        ("driver", "Driver", "Водитель", "Driver", "Motorista"),
        ("moving_helper", "Moving helper", "Помощь с переездом", "Moving helper", "Mudanças"),
    ],
    "tourism": [
        ("tour_consultant", "Tour consultant", "Туристический консультант", "Tour consultant", "Consultor turístico"),
        ("travel_planner", "Travel planner", "Планировщик поездок", "Travel planner", "Planeador de viagens"),
    ],
    "guides": [
        ("city_guide", "City guide", "Городской гид", "City guide", "Guia local"),
        ("museum_guide", "Museum guide", "Музейный гид", "Museum guide", "Guia de museu"),
    ],
    "beauty": [
        ("hairdresser", "Hairdresser", "Парикмахер", "Hairdresser", "Cabeleireiro"),
        ("makeup_artist", "Makeup artist", "Визажист", "Makeup artist", "Maquilhador"),
    ],
    "documents_legalization": [
        ("document_consultant", "Document consultant", "Консультант по документам", "Document consultant", "Consultor documental"),
        ("translator_documents", "Document translator", "Переводчик документов", "Document translator", "Tradutor de documentos"),
    ],
    "auto": [
        ("auto_mechanic", "Auto mechanic", "Автомеханик", "Auto mechanic", "Mecânico auto"),
        ("car_diagnostics", "Car diagnostics", "Диагностика авто", "Car diagnostics", "Diagnóstico auto"),
    ],
    "it_digital": [
        ("automation_specialist", "Automation specialist", "Специалист по автоматизации", "Automation specialist", "Especialista em automação"),
        ("web_developer", "Web developer", "Веб-разработчик", "Web developer", "Programador web"),
    ],
    "education": [
        ("language_tutor", "Language tutor", "Репетитор языка", "Language tutor", "Professor de línguas"),
        ("school_tutor", "School tutor", "Школьный репетитор", "School tutor", "Explicador escolar"),
    ],
    "care": [
        ("babysitter", "Babysitter", "Няня", "Babysitter", "Babysitter"),
        ("elderly_care", "Elderly care", "Уход за пожилыми", "Elderly care", "Cuidados a idosos"),
    ],
    "real_estate": [
        ("real_estate_consultant", "Real estate consultant", "Консультант по недвижимости", "Real estate consultant", "Consultor imobiliário"),
        ("rental_assistant", "Rental assistant", "Помощник по аренде", "Rental assistant", "Assistente de arrendamento"),
    ],
}

LEGAL_DOCUMENTS = [
    ("terms", "Условия использования SGHR Beta", "Продолжая, вы соглашаетесь с правилами SGHR Beta."),
    ("privacy", "Политика конфиденциальности SGHR Beta", "Мы обрабатываем данные для работы сервиса, поиска, связи, модерации и безопасности."),
    ("specialist_consent", "Согласие на публикацию профиля специалиста", "Я согласен, что мой профиль специалиста может быть показан пользователям SGHR."),
    ("geo_consent", "Согласие на использование геолокации", "Я разрешаю использовать город или геолокацию для поиска специалистов по расстоянию."),
    ("translation_consent", "Согласие на автоматический перевод", "Я согласен на автоматический перевод сообщений на язык собеседника."),
]

RATE_LIMIT_RULES = [
    ("user", "start", 10, 3600, "cooldown"),
    ("user", "contact_request", 20, 86400, "temporary_limit"),
    ("user", "chat_message", 30, 86400, "manual_review"),
    ("user", "complaint", 10, 86400, "manual_review"),
    ("user", "geo_change", 10, 86400, "cooldown"),
    ("user", "profile_edit", 30, 86400, "cooldown"),
]


def new_id() -> str:
    return str(uuid.uuid4())


def json_value(value: dict | list | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


async def table_columns(session, table_name: str) -> set[str]:
    result = await session.execute(
        text(
            """
            select column_name
            from information_schema.columns
            where table_schema = 'public'
              and table_name = :table_name
            """
        ),
        {"table_name": table_name},
    )
    return {row[0] for row in result.all()}


def filtered_payload(columns: set[str], payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key in columns}


JSONB_COLUMNS = {
    "metadata",
    "raw_profile",
    "working_days",
    "working_hours",
    "payload",
    "before_state",
    "after_state",
    "details",
    "anonymization_report",
    "report_data",
}


def bind_expr(column: str) -> str:
    if column in JSONB_COLUMNS:
        return f"cast(:{column} as jsonb)"
    return f":{column}"


async def fetch_one_value(session, query: str, params: dict):
    result = await session.execute(text(query), params)
    return result.scalar_one_or_none()

async def get_allowed_check_values(
    session,
    table_name: str,
    column_name: str,
) -> list[str]:
    result = await session.execute(
        text(
            """
            select pg_get_constraintdef(c.oid) as definition
            from pg_constraint c
            join pg_class t on t.oid = c.conrelid
            join pg_namespace n on n.oid = t.relnamespace
            where n.nspname = 'public'
              and t.relname = :table_name
              and c.contype = 'c'
              and pg_get_constraintdef(c.oid) ilike :column_pattern
            """
        ),
        {
            "table_name": table_name,
            "column_pattern": f"%{column_name}%",
        },
    )

    values: list[str] = []
    for row in result:
        definition = row[0] or ""
        values.extend(re.findall(r"'([^']+)'", definition))

    cleaned: list[str] = []
    seen: set[str] = set()

    for value in values:
        if value not in seen:
            seen.add(value)
            cleaned.append(value)

    return cleaned


async def get_specialist_language_level(session) -> str:
    existing = await fetch_one_value(
        session,
        "select level from specialist_languages where level is not null limit 1",
        {},
    )
    if existing:
        return str(existing)

    allowed = await get_allowed_check_values(
        session,
        "specialist_languages",
        "level",
    )

    preferred = [
        "native",
        "advanced",
        "intermediate",
        "basic",
        "beginner",
        "c2",
        "c1",
        "b2",
        "b1",
        "a2",
        "a1",
    ]

    for value in preferred:
        if value in allowed:
            return value

    if allowed:
        return allowed[0]

    raise RuntimeError(
        "Cannot seed specialist_languages: no allowed values found for specialist_languages.level"
    )

async def insert_row(session, table_name: str, payload: dict) -> str:
    columns = await table_columns(session, table_name)
    data = filtered_payload(columns, payload)

    if "id" in columns and "id" not in data:
        data["id"] = new_id()

    for column in JSONB_COLUMNS:
        if column in data and isinstance(data[column], (dict, list)):
            data[column] = json_value(data[column])

    column_sql = ", ".join(data.keys())
    values_sql = ", ".join(bind_expr(column) for column in data.keys())

    await session.execute(
        text(f"insert into {table_name} ({column_sql}) values ({values_sql})"),
        data,
    )

    return str(data.get("id"))


async def update_row(session, table_name: str, row_id: str, payload: dict) -> None:
    columns = await table_columns(session, table_name)
    data = filtered_payload(columns, payload)

    data.pop("id", None)
    if not data:
        return

    for column in JSONB_COLUMNS:
        if column in data and isinstance(data[column], (dict, list)):
            data[column] = json_value(data[column])

    data["row_id"] = row_id
    set_sql = ", ".join(f"{column} = {bind_expr(column)}" for column in data.keys() if column != "row_id")

    await session.execute(
        text(f"update {table_name} set {set_sql} where id = :row_id"),
        data,
    )


async def ensure_by_column(
    session,
    *,
    table_name: str,
    column: str,
    value,
    payload: dict,
) -> str:
    existing_id = await fetch_one_value(
        session,
        f"select id from {table_name} where {column} = :value limit 1",
        {"value": value},
    )

    if existing_id:
        await update_row(session, table_name, str(existing_id), payload)
        return str(existing_id)

    return await insert_row(session, table_name, payload)


async def ensure_tenant(session) -> str:
    env_tenant_id = (os.getenv("DEFAULT_TENANT_ID") or "").strip()
    if env_tenant_id:
        existing_id = await fetch_one_value(
            session,
            "select id from tenants where id = :tenant_id limit 1",
            {"tenant_id": env_tenant_id},
        )
        if existing_id:
            return str(existing_id)

    existing_default = await fetch_one_value(
        session,
        "select id from tenants where name = :name limit 1",
        {"name": "SGHR Beta"},
    )
    if existing_default:
        return str(existing_default)

    tenant_id = env_tenant_id or new_id()
    await insert_row(
        session,
        "tenants",
        {
            "id": tenant_id,
            "name": "SGHR Beta",
            "slug": "sghr-beta",
            "default_language": "ru",
            "default_currency": "EUR",
            "status": "active",
            "metadata": {"source": "seed_beta_data"},
        },
    )
    return tenant_id


async def seed_languages(session) -> None:
    columns = await table_columns(session, "languages")
    if not columns:
        return

    for item in LANGUAGES:
        existing = await fetch_one_value(
            session,
            "select code from languages where code = :code limit 1",
            {"code": item["code"]},
        )
        payload = {
            "code": item["code"],
            "name": item["name"],
            "native_name": item["native_name"],
            "is_active": True,
        }
        if existing:
            data = filtered_payload(columns, payload)
            await session.execute(
                text(
                    """
                    update languages
                    set name = :name,
                        native_name = :native_name,
                        is_active = :is_active
                    where code = :code
                    """
                ),
                data,
            )
        else:
            data = filtered_payload(columns, payload)
            await session.execute(
                text(
                    f"insert into languages ({', '.join(data.keys())}) "
                    f"values ({', '.join(':' + key for key in data.keys())})"
                ),
                data,
            )


async def seed_currencies(session) -> None:
    columns = await table_columns(session, "currencies")
    if not columns:
        return

    for item in CURRENCIES:
        existing = await fetch_one_value(
            session,
            "select code from currencies where code = :code limit 1",
            {"code": item["code"]},
        )
        payload = {
            "code": item["code"],
            "name": item["name"],
            "symbol": item["symbol"],
            "is_active": True,
        }
        if existing:
            data = filtered_payload(columns, payload)
            await session.execute(
                text(
                    """
                    update currencies
                    set name = :name,
                        symbol = :symbol,
                        is_active = :is_active
                    where code = :code
                    """
                ),
                data,
            )
        else:
            data = filtered_payload(columns, payload)
            await session.execute(
                text(
                    f"insert into currencies ({', '.join(data.keys())}) "
                    f"values ({', '.join(':' + key for key in data.keys())})"
                ),
                data,
            )


async def seed_countries_and_cities(session) -> dict[str, str]:
    country_ids: dict[str, str] = {}

    for country in COUNTRIES:
        country_id = await ensure_by_column(
            session,
            table_name="countries",
            column="code",
            value=country["code"],
            payload={
                **country,
                "is_active": True,
                "metadata": {"source": "seed_beta_data"},
            },
        )
        country_ids[country["code"]] = country_id

    for city in CITIES:
        country_id = country_ids[city["country_code"]]
        existing_id = await fetch_one_value(
            session,
            """
            select id
            from cities
            where country_id = :country_id
              and lower(name) = lower(:name)
            limit 1
            """
            ,
            {"country_id": country_id, "name": city["name"]},
        )
        payload = {
            "country_id": country_id,
            "name": city["name"],
            "name_ru": city["name_ru"],
            "name_en": city["name_en"],
            "name_pt": city["name_pt"],
            "latitude": city["latitude"],
            "longitude": city["longitude"],
            "timezone": city["timezone"],
            "is_active": True,
            "metadata": {"source": "seed_beta_data"},
        }

        if existing_id:
            await update_row(session, "cities", str(existing_id), payload)
        else:
            await insert_row(session, "cities", payload)

    return country_ids


async def seed_taxonomy(session) -> dict[str, str]:
    category_ids: dict[str, str] = {}

    for index, (code, name, name_ru, name_en, name_pt) in enumerate(CATEGORIES, start=1):
        category_id = await ensure_by_column(
            session,
            table_name="specialist_categories",
            column="code",
            value=code,
            payload={
                "code": code,
                "name": name,
                "name_ru": name_ru,
                "name_en": name_en,
                "name_pt": name_pt,
                "sort_order": index,
                "is_active": True,
                "metadata": {"source": "seed_beta_data"},
            },
        )
        category_ids[code] = category_id

    for category_code, professions in PROFESSIONS.items():
        category_id = category_ids[category_code]
        for code, name, name_ru, name_en, name_pt in professions:
            await ensure_by_column(
                session,
                table_name="professions",
                column="code",
                value=code,
                payload={
                    "category_id": category_id,
                    "code": code,
                    "name": name,
                    "name_ru": name_ru,
                    "name_en": name_en,
                    "name_pt": name_pt,
                    "normalized_name": name.lower(),
                    "is_active": True,
                    "metadata": {"source": "seed_beta_data"},
                },
            )

    return category_ids


async def seed_legal_documents(session, tenant_id: str) -> None:
    for doc_type, title, content_text in LEGAL_DOCUMENTS:
        existing_id = await fetch_one_value(
            session,
            """
            select id
            from legal_documents
            where tenant_id = :tenant_id
              and doc_type = :doc_type
              and version = :version
              and language = 'ru'
            limit 1
            """,
            {
                "tenant_id": tenant_id,
                "doc_type": doc_type,
                "version": DEFAULT_LEGAL_VERSION,
            },
        )
        payload = {
            "tenant_id": tenant_id,
            "doc_type": doc_type,
            "version": DEFAULT_LEGAL_VERSION,
            "language": "ru",
            "title": title,
            "content_text": content_text,
            "status": "active",
            "effective_from": datetime.utcnow(),
        }
        if existing_id:
            await update_row(session, "legal_documents", str(existing_id), payload)
        else:
            await insert_row(session, "legal_documents", payload)


async def seed_rate_limits(session) -> None:
    for scope, action, limit_count, window_seconds, penalty_action in RATE_LIMIT_RULES:
        existing_id = await fetch_one_value(
            session,
            """
            select id
            from rate_limit_rules
            where scope = :scope
              and action = :action
            limit 1
            """,
            {"scope": scope, "action": action},
        )
        payload = {
            "scope": scope,
            "action": action,
            "limit_count": limit_count,
            "window_seconds": window_seconds,
            "penalty_action": penalty_action,
            "is_active": True,
        }
        if existing_id:
            await update_row(session, "rate_limit_rules", str(existing_id), payload)
        else:
            await insert_row(session, "rate_limit_rules", payload)


async def ensure_telegram_user(
    session,
    *,
    tenant_id: str,
    platform_user_id: str,
    role: str,
    username: str,
    first_name: str,
    last_name: str,
) -> str:
    existing_user_id = await fetch_one_value(
        session,
        """
        select user_id
        from user_accounts
        where platform = 'telegram'
          and platform_user_id = :platform_user_id
        limit 1
        """,
        {"platform_user_id": platform_user_id},
    )
    if existing_user_id:
        user_id = str(existing_user_id)
        await update_row(
            session,
            "users",
            user_id,
            {
                "tenant_id": tenant_id,
                "active_role": role if role in {"super_admin", "admin"} else None,
                "language_code": "ru",
                "status": "active",
            },
        )
    else:
        user_id = await insert_row(
            session,
            "users",
            {
                "tenant_id": tenant_id,
                "active_role": role if role in {"super_admin", "admin"} else None,
                "language_code": "ru",
                "status": "active",
                "metadata": {"source": "seed_beta_data"},
            },
        )
        await insert_row(
            session,
            "user_accounts",
            {
                "user_id": user_id,
                "platform": "telegram",
                "platform_user_id": platform_user_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "display_name": f"{first_name} {last_name}".strip(),
                "language_code": "ru",
                "source": "seed_beta_data",
                "raw_profile": {"seed": True},
            },
        )

    role_id = await fetch_one_value(
        session,
        """
        select id
        from user_roles
        where user_id = :user_id
          and tenant_id = :tenant_id
          and role = :role
        limit 1
        """,
        {"user_id": user_id, "tenant_id": tenant_id, "role": role},
    )

    role_payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "role": role,
        "status": "active",
        "metadata": {"source": "seed_beta_data"},
    }

    if role_id:
        await update_row(session, "user_roles", str(role_id), role_payload)
    else:
        await insert_row(session, "user_roles", role_payload)

    return user_id


async def seed_admin_users(session, tenant_id: str) -> None:
    admin_ids = [
        item.strip()
        for item in (os.getenv("ADMIN_TELEGRAM_IDS") or "").split(",")
        if item.strip()
    ]

    for index, platform_user_id in enumerate(admin_ids, start=1):
        await ensure_telegram_user(
            session,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            role="super_admin",
            username=f"seed_admin_{index}",
            first_name="SGHR",
            last_name="Admin",
        )


async def seed_test_specialists(session, tenant_id: str) -> None:
    should_seed = (os.getenv("SEED_BETA_TEST_SPECIALISTS") or "").lower() in {"1", "true", "yes"}
    environment = (os.getenv("ENVIRONMENT") or "local").lower()

    if not should_seed:
        print("skip test specialists: set SEED_BETA_TEST_SPECIALISTS=true to create staging profiles")
        return

    if environment == "production":
        print("skip test specialists: ENVIRONMENT=production")
        return

    city_id = await fetch_one_value(
        session,
        "select id from cities where name = 'Lisbon' limit 1",
        {},
    )
    country_id = await fetch_one_value(
        session,
        "select country_id from cities where id = :city_id",
        {"city_id": city_id},
    )

    category_rows = await session.execute(
        text(
            """
            select c.id as category_id, p.id as profession_id, p.name as profession_name
            from specialist_categories c
            join professions p on p.category_id = c.id
            where c.is_active is true
              and p.is_active is true
            order by c.sort_order asc, p.name asc
            limit 20
            """
        )
    )
    pairs = category_rows.mappings().all()

    if not city_id or not country_id or not pairs:
        print("skip test specialists: missing city/category/profession seed data")
        return
    
    language_level = await get_specialist_language_level(session)
    for index in range(20):
        pair = pairs[index % len(pairs)]
        platform_user_id = f"seed-specialist-{index + 1}"
        user_id = await ensure_telegram_user(
            session,
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            role="specialist",
            username=platform_user_id,
            first_name="Seed",
            last_name=f"Specialist {index + 1}",
        )

        existing_specialist_id = await fetch_one_value(
            session,
            "select id from specialists where user_id = :user_id limit 1",
            {"user_id": user_id},
        )

        profile_payload = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "category_id": str(pair["category_id"]),
            "profession_id": str(pair["profession_id"]),
            "country_id": str(country_id),
            "city_id": str(city_id),
            "display_name": f"Seed Specialist {index + 1}",
            "short_description": f"Seed beta specialist profile for {pair['profession_name']}.",
            "full_description": f"Seed beta specialist profile created for staging smoke tests. Profession: {pair['profession_name']}.",
            "price_from": Decimal("25.00") + index,
            "price_to": Decimal("50.00") + index,
            "currency": "EUR",
            "price_unit": "service",
            "work_format": "mixed",
            "latitude": Decimal("38.7222524"),
            "longitude": Decimal("-9.1393366"),
            "service_radius_km": 25,
            "is_verified": False,
            "is_premium": index % 5 == 0,
            "is_available": True,
            "priority_score": Decimal("0.00"),
            "rating": Decimal("4.50"),
            "reviews_count": 0,
            "status": "active",
            "metadata": {
                "source": "seed_beta_data",
                "contact_text": "Contact inside SGHR Beta chat",
            },
        }

        if existing_specialist_id:
            specialist_id = str(existing_specialist_id)
            await update_row(session, "specialists", specialist_id, profile_payload)
        else:
            specialist_id = await insert_row(session, "specialists", profile_payload)

        location_id = await fetch_one_value(
            session,
            "select id from specialist_locations where specialist_id = :specialist_id limit 1",
            {"specialist_id": specialist_id},
        )
        location_payload = {
            "tenant_id": tenant_id,
            "specialist_id": specialist_id,
            "country_id": str(country_id),
            "city_id": str(city_id),
            "latitude": Decimal("38.7222524"),
            "longitude": Decimal("-9.1393366"),
            "location_source": "seed",
            "visibility_level": "city",
            "is_current": True,
        }
        if location_id:
            await update_row(session, "specialist_locations", str(location_id), location_payload)
        else:
            await insert_row(session, "specialist_locations", location_payload)

        service_id = await fetch_one_value(
            session,
            "select id from specialist_services where specialist_id = :specialist_id limit 1",
            {"specialist_id": specialist_id},
        )
        service_payload = {
            "tenant_id": tenant_id,
            "specialist_id": specialist_id,
            "title": f"{pair['profession_name']} service",
            "description": "Seed service for SGHR Beta staging smoke tests.",
            "price_from": profile_payload["price_from"],
            "price_to": profile_payload["price_to"],
            "currency": "EUR",
            "price_unit": "service",
            "status": "active",
            "metadata": {"source": "seed_beta_data"},
        }
        if service_id:
            await update_row(session, "specialist_services", str(service_id), service_payload)
        else:
            await insert_row(session, "specialist_services", service_payload)

        for language_code in ["ru", "en"]:
            language_id = await fetch_one_value(
                session,
                """
                select id
                from specialist_languages
                where specialist_id = :specialist_id
                  and language_code = :language_code
                limit 1
                """,
                {"specialist_id": specialist_id, "language_code": language_code},
            )
            language_payload = {
                "specialist_id": specialist_id,
                "language_code": language_code,
                "level": language_level,
            }
            if language_id:
                await update_row(session, "specialist_languages", str(language_id), language_payload)
            else:
                await insert_row(session, "specialist_languages", language_payload)


async def main() -> None:
    async with async_session() as session:
        tenant_id = await ensure_tenant(session)

        await seed_languages(session)
        await seed_currencies(session)
        await seed_countries_and_cities(session)
        await seed_taxonomy(session)
        await seed_legal_documents(session, tenant_id)
        await seed_rate_limits(session)
        await seed_admin_users(session, tenant_id)
        await seed_test_specialists(session, tenant_id)

        await session.commit()

    print("OK: beta seed data is ready")


if __name__ == "__main__":
    asyncio.run(main())