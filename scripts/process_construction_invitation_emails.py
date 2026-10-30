"""Explicit entrypoint; importing this file does not connect or send email."""
import argparse
import asyncio
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Process queued Construction invitation emails.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--once', action='store_true', help='Process at most one eligible invitation job.')
    mode.add_argument('--check-config', action='store_true', help='Validate local settings only; no database or email operations.')
    return parser.parse_args(argv)

async def serve(settings, *, once=False):
    from database.session import async_session, engine
    from services.resend_email_provider import close_resend_http_client
    from services.construction.construction_invitation_worker_runtime import run_invitation_worker, verify_invitation_worker_connection
    stop, loop, installed = asyncio.Event(), asyncio.get_running_loop(), []
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
                installed.append(signum)
            except (NotImplementedError, RuntimeError):
                pass
        if engine.dialect.name != 'postgresql':
            raise ValueError('Invitation runtime requires PostgreSQL.')
        async with async_session() as session:
            async with session.begin():
                await verify_invitation_worker_connection(session)
        count = await run_invitation_worker(session_factory=async_session, settings=settings, stop=stop, once=once)
        print('INVITATION_WORKER_STOPPED: processed_jobs=' + str(count))
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
        from services.construction.construction_invitation_worker_runtime import InvitationWorkerSettings
        settings = InvitationWorkerSettings.from_env()
    except Exception:
        print('STOP: Invitation worker settings are missing or invalid. Check EMAIL_*, WEB_APP_URL, RESEND_API_KEY, API_IDEMPOTENCY_ENCRYPTION_KEY and CONSTRUCTION_INVITATION_*.', file=sys.stderr)
        return 2
    if args.check_config:
        print('INVITATION_WORKER_CONFIG: READY; no database connections or email delivery attempted')
        return 0
    try:
        asyncio.run(serve(settings, once=args.once))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        # Provider/SQL exception text can contain message bodies or URLs.
        print('STOP: Invitation worker failed; transaction rolled back or connection closed. No private details logged.', file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
