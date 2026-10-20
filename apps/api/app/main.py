from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import SessionLocal, migrate
from app.domain import create_snapshot_run, default_as_of, fill_pending, materialize_reinvest, now_sh
from app.market import sync_all
from app.models import JobRun
from app.routes import router
from app.seed import seed


def scheduled_valuation() -> None:
    session = SessionLocal()
    moment = now_sh()
    job = JobRun(kind="schedule", status="running", started_at=moment, message="")
    session.add(job)
    session.commit()
    try:
        sync_all(session)
        session.commit()
        as_of = default_as_of(session, now_sh())
        if as_of is None:
            job.status = "skipped"
            job.message = "没有可估值的交易日"
        else:
            filled = fill_pending(session, as_of, now_sh())
            reinvested = materialize_reinvest(session, as_of, now_sh())
            session.commit()
            run = create_snapshot_run(session, as_of, now_sh())
            job.status = run.status
            job.message = run.error or f"快照至 {as_of.isoformat()}，成交 {len(filled)} 笔，分红再投资 {len(reinvested)} 笔"
        job.finished_at = now_sh()
        session.commit()
    except Exception as exc:
        session.rollback()
        job = session.get(JobRun, job.id)
        if job:
            job.status = "failed"
            job.message = str(exc)
            job.finished_at = now_sh()
            session.commit()
    finally:
        session.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate()
    session = SessionLocal()
    try:
        seed(session)
    finally:
        session.close()
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(scheduled_valuation, CronTrigger(day_of_week="mon-fri", hour=18, minute=30))
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="配置沙盘", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
