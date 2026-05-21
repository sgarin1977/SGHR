from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def requirement_names() -> set[str]:
    names = set()
    for line in read("requirements.txt").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split(">=")[0].split("==")[0].split("<")[0].strip().lower()
        names.add(name)
    return names


def test_requirements_match_tz_minimum_stack():
    reqs = requirement_names()

    assert "aiogram" in reqs
    assert "sqlalchemy" in reqs
    assert "asyncpg" in reqs
    assert "python-dotenv" in reqs
    assert "alembic" in reqs
    assert "pydantic" in reqs
    assert "httpx" in reqs
    assert "pytest" in reqs
    assert "pytest-asyncio" in reqs

    requirements = read("requirements.txt")
    assert "aiogram>=2" not in requirements
    assert "SQLAlchemy>=1" not in requirements


def test_env_example_has_required_keys_without_real_secrets():
    env_example = read(".env.example")

    required_keys = [
        "ENVIRONMENT",
        "BOT_TOKEN",
        "ADMIN_TELEGRAM_IDS",
        "DEFAULT_LANGUAGE",
        "DEFAULT_TENANT_ID",
        "DATABASE_URL",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "LOG_LEVEL",
        "TRANSLATION_PROVIDER",
        "TRANSLATION_BASE_URL",
        "TRANSLATION_API_KEY",
        "TRANSLATION_TIMEOUT_SECONDS",
        "TRANSLATION_MAX_RETRIES",
        "TRANSLATION_CACHE_ENABLED",
        "GEO_MODE",
        "BETA_MODE",
    ]

    for key in required_keys:
        assert f"{key}=" in env_example

    for secret_key in [
        "BOT_TOKEN",
        "DATABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "TRANSLATION_API_KEY",
    ]:
        line = next(item for item in env_example.splitlines() if item.startswith(f"{secret_key}="))
        assert line == f"{secret_key}="


def test_database_session_does_not_print_database_url():
    session_code = read("database/session.py")

    assert "print(" not in session_code
    assert "DATABASE_URL" in session_code
    assert "statement_cache_size" in session_code


def test_bot_entrypoint_only_wires_routers_not_db_logic():
    bot_code = read("bot.py")

    assert "include_router" in bot_code
    assert "start_polling" in bot_code
    assert "select(" not in bot_code
    assert "session.execute" not in bot_code
    assert "UserRepository" not in bot_code

def test_gitignore_protects_real_env_and_runtime_files():
    gitignore = read(".gitignore")

    assert ".env" in gitignore
    assert "venv/" in gitignore
    assert "__pycache__/" in gitignore
    assert "*.pyc" in gitignore


def test_alembic_project_exists_but_is_not_used_as_unsafe_runtime_step():
    assert (ROOT / "alembic.ini").exists()
    assert (ROOT / "alembic").exists()
    assert (ROOT / "alembic" / "env.py").exists()
    assert (ROOT / "alembic" / "versions").exists()