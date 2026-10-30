import asyncio
import importlib.util
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4
from cryptography.fernet import Fernet
import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import Session
from sqlalchemy.dialects import postgresql
from api.settings import ApiConfigurationError
from database.models import ConstructionBackgroundJobRecord
from services.construction.construction_jobs import ConstructionBackgroundJobWorker, ConstructionJobPermanentError, ConstructionJobRetryError
from services.construction.construction_invitation_worker_runtime import (
    InvitationWorkerSettings, InvitationEmailQueue, build_invitation_runtime_worker,
    process_invitation_once, run_invitation_worker, verify_invitation_worker_connection,
)

@pytest.fixture
def config_values():
    return dict(EMAIL_PROVIDER='resend', EMAIL_FROM='invite@example.com', WEB_APP_URL='https://app.example.com',
        RESEND_API_KEY='re_private_test_value', API_IDEMPOTENCY_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        CONSTRUCTION_INVITATION_RECIPIENT_PAGE_URL='https://app.example.com/construction/recipient')

@pytest.fixture
def settings(config_values):
    return InvitationWorkerSettings.from_env(config_values)

def test_configuration_and_redaction(settings, config_values):
    assert settings.lease_seconds == 120 and settings.job_timeout_seconds == 30
    assert settings.email.from_address == 'invite@example.com'
    assert config_values['RESEND_API_KEY'] not in repr(settings)
    assert config_values['API_IDEMPOTENCY_ENCRYPTION_KEY'] not in repr(settings)

@pytest.mark.parametrize('name,value',[
    ('CONSTRUCTION_INVITATION_RECIPIENT_PAGE_URL',''),
    ('CONSTRUCTION_INVITATION_RECIPIENT_PAGE_URL','http://app.example.com/path'),
    ('CONSTRUCTION_INVITATION_RECIPIENT_PAGE_URL','https://foreign.example.com/path'),
    ('CONSTRUCTION_INVITATION_RECIPIENT_PAGE_URL','https://app.example.com/path?token=private'),
    ('CONSTRUCTION_INVITATION_WORKER_POLL_SECONDS','nan'),
    ('CONSTRUCTION_INVITATION_WORKER_POLL_SECONDS','0'),
    ('CONSTRUCTION_INVITATION_WORKER_LEASE_SECONDS','20'),
    ('CONSTRUCTION_INVITATION_WORKER_JOB_TIMEOUT_SECONDS','301'),
    ('CONSTRUCTION_INVITATION_EMAIL_LANGUAGE','unknown'),
    ('API_IDEMPOTENCY_ENCRYPTION_KEY',''),
])
def test_invalid_config_rejected_without_resources(config_values,name,value):
    config_values[name]=value
    with pytest.raises((ValueError, ApiConfigurationError)): InvitationWorkerSettings.from_env(config_values)

@pytest.fixture
def queue_db():
    c=SimpleNamespace(engine=create_engine('sqlite://'), now=datetime(2026,10,20,12,tzinfo=timezone.utc),
        tenant=uuid4(), user=uuid4(), statements=[], commits=0, rollbacks=0, fail_commit=False, sessions=[])
    with c.engine.begin() as conn:
        conn.execute(text('CREATE TABLE users (id CHAR(32) PRIMARY KEY)'))
        conn.execute(text('CREATE TABLE tenants (id CHAR(32) PRIMARY KEY)'))
    ConstructionBackgroundJobRecord.__table__.create(c.engine)
    with Session(c.engine) as sync:
        for number, kind, available, lease, status in (
            (1,'EXPORT_PDF',c.now,None,'QUEUED'),
            (2,'INVITATION_EMAIL',c.now,None,'QUEUED'),
            (3,'INVITATION_EMAIL',c.now+timedelta(hours=1),None,'QUEUED'),
            (4,'INVITATION_EMAIL',c.now,c.now+timedelta(hours=1),'RUNNING')):
            sync.add(ConstructionBackgroundJobRecord(id=UUID(int=number),tenant_id=c.tenant,user_id=c.user,
                resource_type='construction_invitation',resource_id=uuid4(),job_type=kind,status=status,
                idempotency_key=str(number),attempts=0,available_at=available,lease_expires_at=lease,progress=0,
                created_at=c.now,updated_at=c.now))
        sync.commit()

    class Adapter:
        def __init__(self): self.sync=Session(c.engine); c.sessions.append(self)
        @property
        def no_autoflush(self): return self.sync.no_autoflush
        def in_transaction(self): return self.sync.in_transaction()
        async def execute(self, statement):
            c.statements.append(str(statement.compile(dialect=postgresql.dialect())))
            return self.sync.execute(statement)
        async def flush(self): self.sync.flush()
        @asynccontextmanager
        async def begin(self):
            self.sync.begin()
            try:
                yield
                if c.fail_commit: raise RuntimeError('private commit failure')
                self.sync.commit(); c.commits+=1
            except BaseException:
                self.sync.rollback(); c.rollbacks+=1
                raise
    @asynccontextmanager
    async def factory():
        session=Adapter()
        try: yield session
        finally: session.sync.close()
    c.factory=factory
    def states():
        with Session(c.engine) as session:
            return {x.id.int:(x.status,x.attempts,x.error_code) for x in session.scalars(select(ConstructionBackgroundJobRecord))}
    c.states=states
    yield c
    c.engine.dispose()

def make_factory(c, handler):
    def factory(*,session,settings,lease_owner):
        return ConstructionBackgroundJobWorker(queue=InvitationEmailQueue(session), handlers={'INVITATION_EMAIL':handler},
            lease_owner=lease_owner, lease_duration=timedelta(seconds=settings.lease_seconds), clock=lambda:c.now)
    return factory

@pytest.mark.asyncio
async def test_real_sql_claim_send_outcome_single_commit_and_other_types_untouched(queue_db,settings):
    c=queue_db
    async def send(job):
        assert c.sessions[-1].in_transaction()
        assert job.tenant_id==c.tenant and job.id==UUID(int=2)
        assert c.commits==0
        return 'construction_invitation:'+str(job.resource_id)
    result=await process_invitation_once(session_factory=c.factory, settings=settings, lease_owner='mail-worker',
        worker_factory=make_factory(c,send))
    assert result==UUID(int=2) and c.commits==1 and c.rollbacks==0
    assert c.states()=={1:('QUEUED',0,None),2:('READY',1,None),3:('QUEUED',0,None),4:('RUNNING',0,None)}
    claim=next(s for s in c.statements if s.startswith('SELECT '))
    assert 'FOR UPDATE SKIP LOCKED' in claim and 'job_type =' in claim

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ready','permanent','retry'])
async def test_existing_outcome_protocol_commits_once(queue_db,settings,mode):
    c=queue_db
    async def handler(job):
        if mode=='permanent': raise ConstructionJobPermanentError(error_code='DELIVERY_UNAVAILABLE')
        if mode=='retry': raise ConstructionJobRetryError(error_code='TRY_LATER',available_at=c.now+timedelta(minutes=1))
        return 'safe-result'
    await process_invitation_once(session_factory=c.factory,settings=settings,lease_owner='mail',worker_factory=make_factory(c,handler))
    assert c.states()[2][0]==dict(ready='READY',permanent='FAILED',retry='RETRY')[mode]
    assert c.commits==1 and c.rollbacks==0

@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['worker','commit','cancel','timeout'])
async def test_real_claim_flush_rolls_back_on_runtime_failure(queue_db,settings,failure):
    from dataclasses import replace
    c=queue_db; before=c.states()
    if failure=='commit': c.fail_commit=True
    if failure=='timeout': settings=replace(settings,job_timeout_seconds=.01)
    def factory(*,session,settings,lease_owner):
        async def run_once():
            job=await InvitationEmailQueue(session).claim_next(now=c.now,lease_owner=lease_owner,lease_expires_at=c.now+timedelta(minutes=2))
            assert job.id==UUID(int=2)
            if failure=='worker': raise RuntimeError('private worker failure')
            if failure=='cancel': raise asyncio.CancelledError()
            if failure=='timeout': await asyncio.Event().wait()
            return job.id
        return SimpleNamespace(run_once=run_once)
    expected=asyncio.CancelledError if failure=='cancel' else TimeoutError if failure=='timeout' else RuntimeError
    with pytest.raises(expected):
        await process_invitation_once(session_factory=c.factory,settings=settings,lease_owner='mail',worker_factory=factory)
    assert c.states()==before and c.commits==0 and c.rollbacks==1

@pytest.mark.asyncio
async def test_stop_before_start_opens_no_session(queue_db,settings):
    stop=asyncio.Event(); stop.set()
    assert await run_invitation_worker(session_factory=queue_db.factory,settings=settings,stop=stop)==0
    assert queue_db.sessions==[]

@pytest.mark.asyncio
async def test_once_processes_at_most_one_and_returns(queue_db,settings):
    c=queue_db
    async def handler(job): return 'delivered'
    count=await run_invitation_worker(session_factory=c.factory,settings=settings,stop=asyncio.Event(),once=True,
        worker_factory=make_factory(c,handler))
    assert count==1 and len(c.sessions)==1 and c.commits==1

@pytest.mark.asyncio
async def test_stop_drains_current_job_without_claiming_another(queue_db,settings):
    c=queue_db; stop=asyncio.Event()
    async def handler(job): stop.set(); return 'delivered'
    count=await run_invitation_worker(session_factory=c.factory,settings=settings,stop=stop,
        worker_factory=make_factory(c,handler))
    assert count==1 and len(c.sessions)==1 and c.states()[2][0]=='READY'

@pytest.mark.asyncio
async def test_empty_queue_stop_wakes_idle_poll(queue_db,settings):
    c=queue_db; stop=asyncio.Event()
    def factory(**kwargs):
        async def run_once():
            asyncio.get_running_loop().call_soon(stop.set)
            return None
        return SimpleNamespace(run_once=run_once)
    assert await asyncio.wait_for(run_invitation_worker(session_factory=c.factory,settings=settings,stop=stop,
        worker_factory=factory),timeout=.5)==0
    assert len(c.sessions)==1

def test_factory_keeps_approved_handler_and_scopes_claims(settings):
    session=SimpleNamespace(in_transaction=lambda:True)
    worker=build_invitation_runtime_worker(session=session,settings=settings,lease_owner='mail',
        provider=SimpleNamespace(send_email=AsyncMock()))
    assert isinstance(worker.queue,InvitationEmailQueue)
    assert set(worker.handlers)=={'INVITATION_EMAIL'}
    assert worker.handlers['INVITATION_EMAIL'].session is session
    assert worker.handlers['INVITATION_EMAIL'].loader.session is session

@pytest.mark.asyncio
@pytest.mark.parametrize('role',['anon','authenticated','service_role','construction_api'])
async def test_runtime_connection_cannot_be_browser_or_construction(role):
    result=Mock(); result.mappings.return_value.one.return_value=dict(role_name=role,read_only='off')
    with pytest.raises(ValueError):
        await verify_invitation_worker_connection(SimpleNamespace(execute=AsyncMock(return_value=result)))

def load_cli():
    path=Path(__file__).resolve().parents[2]/'scripts/process_construction_invitation_emails.py'
    spec=importlib.util.spec_from_file_location('invitation_runtime_cli',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

def test_cli_check_config_never_starts_worker(monkeypatch,settings,capsys):
    cli=load_cli(); called=[]
    monkeypatch.setattr(InvitationWorkerSettings,'from_env',lambda:settings)
    monkeypatch.setattr(cli,'serve',lambda *a,**k:called.append(True))
    assert cli.main(['--check-config'])==0 and called==[]
    output=capsys.readouterr()
    assert 'READY' in output.out and settings.crypto.encryption_key not in output.out

def test_cli_config_and_runtime_failures_do_not_log_secrets(monkeypatch,capsys,settings):
    cli=load_cli()
    monkeypatch.setattr(InvitationWorkerSettings,'from_env',Mock(side_effect=ValueError('private-secret')))
    assert cli.main(['--once'])==2
    assert 'private-secret' not in capsys.readouterr().err
    monkeypatch.setattr(InvitationWorkerSettings,'from_env',lambda:settings)
    async def failed(*args,**kwargs): raise RuntimeError('token=private-secret')
    monkeypatch.setattr(cli,'serve',failed)
    assert cli.main(['--once'])==1
    assert 'private-secret' not in capsys.readouterr().err

@pytest.mark.asyncio
@pytest.mark.parametrize('failed',[False,True])
async def test_serve_always_closes_http_and_engine_and_removes_signal_handlers(monkeypatch,settings,failed):
    import database.session as db
    import services.resend_email_provider as email
    import services.construction.construction_invitation_worker_runtime as runtime
    cli=load_cli()
    @asynccontextmanager
    async def transaction(): yield
    @asynccontextmanager
    async def session(): yield SimpleNamespace(begin=transaction)
    engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),dispose=AsyncMock())
    monkeypatch.setattr(db,'async_session',session)
    monkeypatch.setattr(db,'engine',engine,raising=False)
    close=AsyncMock(); monkeypatch.setattr(email,'close_resend_http_client',close)
    verify=AsyncMock(); monkeypatch.setattr(runtime,'verify_invitation_worker_connection',verify)
    run=AsyncMock(return_value=1,side_effect=RuntimeError('private') if failed else None)
    monkeypatch.setattr(runtime,'run_invitation_worker',run)
    loop=asyncio.get_running_loop(); added=Mock(); removed=Mock()
    monkeypatch.setattr(loop,'add_signal_handler',added)
    monkeypatch.setattr(loop,'remove_signal_handler',removed)
    if failed:
        with pytest.raises(RuntimeError): await cli.serve(settings,once=True)
    else: await cli.serve(settings,once=True)
    verify.assert_awaited_once(); run.assert_awaited_once(); close.assert_awaited_once(); engine.dispose.assert_awaited_once()
    assert added.call_count==removed.call_count==2
