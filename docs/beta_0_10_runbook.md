# SGHR Beta 0.10 Runbook

## Назначение

Этот runbook описывает минимальные проверки перед push, merge или deploy SGHR Beta 0.10.

## 1. Состояние Git

Выполнить:

git branch --show-current
git status --short
git log --oneline -3

Ожидаемый результат:

- текущая ветка является правильной release-веткой;
- нет неожиданных локальных изменений;
- нет случайно добавленных debug/temp файлов.

## 2. Проверка компиляции

Выполнить:

python -m py_compile bot.py config.py database/models.py handlers/search.py services/geo_search.py services/contact_chat.py

Ожидаемый результат:

- команда завершается без ошибок;
- нет syntax/import ошибок.

## 3. Проверка критичных файлов

Выполнить:

wc -l services/geo_service.py database/repositories/search.py fsm/specialist_form.py

Ожидаемый результат:

- файлы не пустые;
- ориентировочные размеры:
  - services/geo_service.py около 144 строк;
  - database/repositories/search.py около 607 строк;
  - fsm/specialist_form.py около 966 строк.

## 4. Основной набор тестов

Выполнить:

PYTHONPATH=. pytest

Ожидаемый результат:

- все тесты проходят;
- известные deprecation warnings допустимы для Beta.

## 5. Smoke-тесты

Выполнить:

PYTHONPATH=. python scripts/smoke_test_db.py
PYTHONPATH=. python scripts/smoke_test_geo.py
PYTHONPATH=. python scripts/smoke_test_translation.py

Ожидаемый результат:

- DB smoke проходит;
- geo provider находит и подтверждает city/country;
- translation smoke проходит.

## 6. Проверка seed data

Выполнить:

python -m py_compile scripts/seed_beta_data.py
SEED_BETA_TEST_SPECIALISTS=true PYTHONPATH=. python scripts/seed_beta_data.py

Ожидаемый результат:

- seed script компилируется;
- seed script завершается без DB constraint ошибок;
- legal docs, rate limit rules, admin/bootstrap data и optional test specialists присутствуют.

Важно:

- не запускать test specialist seed в production без явного решения.

## 7. Проверка Alembic baseline

Выполнить:

PYTHONPATH=. python scripts/check_alembic_version.py

Ожидаемый результат:

cf1a295961d9

Значение:

- текущая Supabase schema зафиксирована как Alembic baseline;
- все будущие изменения структуры БД должны идти через Alembic migrations.

## 8. Проверка запуска бота

Выполнить:

python bot.py

Ожидаемые логи:

bot_starting
bot_routers_registered
bot_polling_start
Start polling
Run polling for bot

Остановить через Ctrl+C.

Ожидаемые shutdown logs:

Received SIGINT signal
Polling stopped

## 9. Проверка файловых логов

Выполнить:

tail -n 30 logs/bot.log

Ожидаемый результат:

- последние startup logs присутствуют;
- INFO/WARNING/ERROR logs пишутся в файл.

## 10. Ручная проверка Telegram flow

Проверить вручную в Telegram:

- /start;
- legal consent перед регистрацией специалиста;
- регистрация специалиста;
- выбор города через ручной ввод;
- выбор города через Telegram location;
- единый экран search filters summary;
- открытие карточки специалиста;
- contact request;
- accept/reject со стороны специалиста;
- отправка сообщения в thread;
- admin approve/reject specialist;
- admin complaints screen;
- billing/manual payment flow, если monetization входит в release.

## 11. Известные ограничения Beta 0.10

Следующие пункты не являются blocker для controlled Beta, если они явно приняты как out of scope текущего release:

- полный reviews flow;
- полный favorites UX/list screen;
- полный GDPR/DSR self-service flow;
- file storage moderation workflow;
- полноценная support ticket system;
- полная история migrations до Alembic baseline.

## 12. Правило deploy

Не deploy-ить, если падает любой из пунктов:

- compile check;
- main test suite;
- DB smoke;
- bot start;
- critical contact/search/moderation flows.

GitHub не считается источником истины, пока локально не прошли compile, tests, smoke и bot start.