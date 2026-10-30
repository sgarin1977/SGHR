"""Explicit client-link email worker; import never opens resources."""
import argparse
import asyncio
from pathlib import Path
import signal
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Process client-link confirmation emails.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--check-config", action="store_true")
    return parser.parse_args(argv)

async def serve(settings, *, once=False):
    from database.session import async_session, engine
    from services.resend_email_provider import close_resend_http_client
    from services.construction.construction_client_link_email_runtime import run_client_link_email_worker
    stop = asyncio.Event()
    loop, installed = asyncio.get_running_loop(), []
    try:
        if engine.dialect.name != "postgresql":
            raise ValueError("The email worker requires PostgreSQL.")
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
                installed.append(signum)
            except (NotImplementedError, RuntimeError):
                pass
        count = await run_client_link_email_worker(
            session_factory=async_session, settings=settings,
            stop=stop, once=once,
        )
        print("CLIENT_LINK_EMAIL_WORKER_STOPPED: processed_jobs=" + str(count))
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)
        try:
            await close_resend_http_client()
        finally:
            await engine.dispose()

def main(argv=None):
    args = parse_args(argv)
    try:
        from services.construction.construction_client_link_email_runtime import ClientLinkEmailSettings
        settings = ClientLinkEmailSettings.from_env()
    except Exception:
        print(
            "STOP: Check EMAIL_*, WEB_APP_URL, RESEND_API_KEY, "
            "API_IDEMPOTENCY_ENCRYPTION_KEY and CONSTRUCTION_CLIENT_LINK_*.",
            file=sys.stderr,
        )
        return 2
    if args.check_config:
        print("CLIENT_LINK_EMAIL_CONFIG: READY; no database or email operations")
        return 0
    try:
        asyncio.run(serve(settings, once=args.once))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(
            "STOP: Client-link email worker failed; transaction rolled back "
            "or connection closed. Private details omitted.",
            file=sys.stderr,
        )
        return 1

if __name__ == "__main__":
    raise SystemExit(main())

# client_link_email_worker_v1
