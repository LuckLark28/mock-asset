"""HTTP 接口。金额在 JSON 里用字符串，避免浮点误差。"""

from __future__ import annotations

import csv
import io
import shutil
import sqlite3
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import DB_PATH, get_session
from app.domain import (
    DomainError,
    activate_plan,
    add_cashflow,
    archive_plan,
    build_target_preview,
    cancel_order,
    create_account,
    create_snapshot_run,
    current_run,
    default_as_of,
    estimate_view,
    fill_pending,
    materialize_reinvest,
    now_sh,
    save_weight_version,
    submission_session,
    submit_order,
    the_account,
    weight_version_on,
)
from app.analytics import (
    brinson,
    execute_task,
    ignore_task,
    read_settings,
    refresh_tasks,
    repair_bar,
    risk_free_rate,
    risk_metrics,
    run_backtest,
    save_fx,
    save_settings,
    set_class_benchmark,
    task_stats,
    trade_outcome,
)
from app.habits import (
    access_granted,
    apply_stress,
    auth_status,
    checklist_items,
    clear_password,
    compare_plans,
    confirm_checklist,
    login,
    month_calendar,
    period_report,
    save_checklist,
    set_password,
    stress_catalog,
)
from app.models import (
    AllocationPlan,
    CashFlow,
    DailySnapshot,
    DecisionLog,
    Instrument,
    JobRun,
    PlanAsset,
    PlanWeightVersion,
    SnapshotRun,
    Trade,
)

router = APIRouter(prefix="/api")


def require_access(request: Request, session: Session = Depends(get_session)) -> None:
    if request.url.path in {"/api/health", "/api/auth/status", "/api/auth/login"}:
        return
    if not access_granted(session, request.headers.get("x-sandbox-token")):
        raise HTTPException(status_code=401, detail="需要访问密码")


router.dependencies.append(Depends(require_access))


def enc(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: enc(item) for key, item in value.items()}
    if isinstance(value, list):
        return [enc(item) for item in value]
    return value


def fail(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


class InstrumentIn(BaseModel):
    symbol: str
    name: str
    asset_class: str
    exchange: str = "SH"


class WeightIn(BaseModel):
    instrument_id: int | None = None
    asset_class: str
    target_weight: Decimal


class PlanIn(BaseModel):
    name: str
    description: str = ""
    strategy_tag: str = ""
    rebalance_threshold: Decimal = Decimal("0.05")
    assets: list[WeightIn]


class AccountIn(BaseModel):
    name: str
    initial_cash: Decimal
    plan_id: int


class CashIn(BaseModel):
    direction: str
    amount: Decimal
    note: str = ""


class LogIn(BaseModel):
    skip: bool = False
    confirm_skip: bool = False
    reason: str = ""
    hypothesis: str = ""
    exit_condition: str = ""
    confidence: int | None = None
    emotion_tag: str = ""


class OrderIn(BaseModel):
    instrument_id: int
    side: str
    quantity: int | None = None
    amount: Decimal | None = None
    log: LogIn = Field(default_factory=LogIn)
    checklist: list[str] = Field(default_factory=list)


class BatchIn(BaseModel):
    log: LogIn = Field(default_factory=LogIn)
    checklist: list[str] = Field(default_factory=list)


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/bootstrap")
def bootstrap(session: Session = Depends(get_session)):
    moment = now_sh()
    account = the_account(session)
    plans = session.scalars(select(AllocationPlan).order_by(AllocationPlan.id)).all()
    instruments = session.scalars(select(Instrument).order_by(Instrument.tradable.desc(), Instrument.symbol)).all()
    return enc(
        {
            "disclaimer": "模拟练习，不构成投资建议。成交使用真实收盘价，历史净值不代表未来收益。",
            "now": moment,
            "next_session": submission_session(session, moment),
            "account": None
            if not account
            else {"id": account.id, "name": account.name, "created_on": account.created_on, "base_currency": account.base_currency},
            "plans": [_plan_dict(session, plan) for plan in plans],
            "instruments": [
                {
                    "id": item.id,
                    "symbol": item.symbol,
                    "name": item.name,
                    "asset_class": item.asset_class,
                    "exchange": item.exchange,
                    "tradable": item.tradable,
                    "lot_size": item.lot_size,
                }
                for item in instruments
            ],
        }
    )


def _plan_dict(session: Session, plan: AllocationPlan) -> dict:
    version = weight_version_on(session, plan.id, date(9999, 12, 31))
    assets = []
    if version:
        for asset in version.assets:
            assets.append(
                {
                    "instrument_id": asset.instrument_id,
                    "symbol": asset.instrument.symbol if asset.instrument else "CASH",
                    "name": asset.instrument.name if asset.instrument else "现金",
                    "asset_class": asset.asset_class,
                    "target_weight": asset.target_weight,
                }
            )
    return {
        "id": plan.id,
        "name": plan.name,
        "description": plan.description,
        "strategy_tag": plan.strategy_tag,
        "benchmark": plan.benchmark.name,
        "rebalance_threshold": plan.rebalance_threshold,
        "status": plan.status,
        "is_primary": plan.is_primary,
        "effective_on": version.effective_on if version else None,
        "assets": assets,
    }


@router.post("/instruments")
def add_instrument(body: InstrumentIn, session: Session = Depends(get_session)):
    symbol = body.symbol.strip()
    if session.scalar(select(Instrument).where(Instrument.symbol == symbol)):
        raise HTTPException(status_code=400, detail="代码已存在")
    if body.asset_class not in {"equity", "bond", "commodity"}:
        raise HTTPException(status_code=400, detail="资产类别无效")
    item = Instrument(
        symbol=symbol,
        name=body.name.strip(),
        asset_class=body.asset_class,
        exchange=body.exchange.upper(),
        lot_size=100,
        tradable=True,
    )
    session.add(item)
    session.commit()
    return enc({"id": item.id})


@router.post("/plans")
def create_plan(body: PlanIn, session: Session = Depends(get_session)):
    benchmark_id = session.scalar(select(AllocationPlan.benchmark_id).limit(1))
    if not benchmark_id:
        raise HTTPException(status_code=400, detail="基准尚未初始化")
    moment = now_sh()
    plan = AllocationPlan(
        name=body.name.strip(),
        description=body.description.strip(),
        strategy_tag=body.strategy_tag.strip(),
        benchmark_id=benchmark_id,
        rebalance_threshold=body.rebalance_threshold,
        status="active",
        is_primary=False,
        created_at=moment,
    )
    session.add(plan)
    session.flush()
    try:
        save_weight_version(session, plan, [item.model_dump() for item in body.assets], moment.date(), moment)
    except DomainError as exc:
        session.rollback()
        raise fail(exc) from exc
    session.commit()
    return enc({"id": plan.id})


@router.put("/plans/{plan_id}")
def update_plan(plan_id: int, body: PlanIn, session: Session = Depends(get_session)):
    plan = session.get(AllocationPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    moment = now_sh()
    plan.name = body.name.strip()
    plan.description = body.description.strip()
    plan.strategy_tag = body.strategy_tag.strip()
    plan.rebalance_threshold = body.rebalance_threshold
    try:
        save_weight_version(session, plan, [item.model_dump() for item in body.assets], moment.date(), moment)
    except DomainError as exc:
        session.rollback()
        raise fail(exc) from exc
    session.commit()
    return enc({"id": plan.id, "effective_on": moment.date()})


@router.post("/plans/{plan_id}/activate")
def activate(plan_id: int, session: Session = Depends(get_session)):
    try:
        activate_plan(session, plan_id, now_sh())
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return {"ok": True}


@router.post("/plans/{plan_id}/archive")
def archive(plan_id: int, session: Session = Depends(get_session)):
    try:
        archive_plan(session, plan_id)
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return {"ok": True}


@router.post("/account")
def open_account(body: AccountIn, session: Session = Depends(get_session)):
    try:
        account = create_account(session, body.name, body.initial_cash, body.plan_id, now_sh())
    except DomainError as exc:
        session.rollback()
        raise fail(exc) from exc
    session.commit()
    return enc({"id": account.id})


@router.post("/cashflows")
def cashflow(body: CashIn, session: Session = Depends(get_session)):
    try:
        flow = add_cashflow(session, body.direction, body.amount, now_sh(), body.note)
    except DomainError as exc:
        session.rollback()
        raise fail(exc) from exc
    session.commit()
    return enc({"id": flow.id})


@router.get("/cashflows")
def list_cashflows(session: Session = Depends(get_session)):
    rows = session.scalars(select(CashFlow).order_by(CashFlow.occurred_on.desc(), CashFlow.id.desc())).all()
    return enc(
        [
            {"id": row.id, "direction": row.direction, "amount": row.amount, "occurred_on": row.occurred_on, "note": row.note}
            for row in rows
        ]
    )


@router.post("/orders")
def create_order(body: OrderIn, session: Session = Depends(get_session)):
    try:
        confirmed = confirm_checklist(session, body.checklist)
        trade = submit_order(
            session,
            body.instrument_id,
            body.side,
            now_sh(),
            body.log.model_dump(),
            quantity=body.quantity,
            amount=body.amount,
            checklist_json=confirmed,
        )
    except DomainError as exc:
        session.rollback()
        raise fail(exc) from exc
    session.commit()
    return enc({"id": trade.id, "status": trade.status, "trade_date": trade.trade_date, "reject_reason": trade.reject_reason})


@router.post("/orders/{trade_id}/cancel")
def cancel(trade_id: int, session: Session = Depends(get_session)):
    try:
        cancel_order(session, trade_id)
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return {"ok": True}


@router.get("/orders")
def list_orders(session: Session = Depends(get_session)):
    rows = session.scalars(select(Trade).options(selectinload(Trade.instrument), selectinload(Trade.log)).order_by(Trade.id.desc())).all()
    return enc([_trade_dict(row) for row in rows])


def _trade_dict(row: Trade) -> dict:
    return {
        "id": row.id,
        "symbol": row.instrument.symbol,
        "name": row.instrument.name,
        "side": row.side,
        "status": row.status,
        "trade_date": row.trade_date,
        "quantity_requested": row.quantity_requested,
        "amount_requested": row.amount_requested,
        "quantity": row.quantity,
        "price": row.price,
        "amount": row.amount,
        "reject_reason": row.reject_reason,
        "is_temporary": row.is_temporary,
        "source": row.source,
        "submitted_at": row.submitted_at,
        "log": None
        if not row.log
        else {
            "reason": row.log.reason,
            "hypothesis": row.log.hypothesis,
            "exit_condition": row.log.exit_condition,
            "confidence": row.log.confidence,
            "emotion_tag": row.log.emotion_tag,
            "skipped": row.log.skipped,
        },
    }


@router.post("/rebalance/preview")
def preview(session: Session = Depends(get_session)):
    try:
        return enc(build_target_preview(session, now_sh()))
    except DomainError as exc:
        raise fail(exc) from exc


@router.post("/rebalance/submit")
def submit_rebalance(body: BatchIn, session: Session = Depends(get_session)):
    try:
        preview_data = build_target_preview(session, now_sh())
        confirmed = confirm_checklist(session, body.checklist)
        created = []
        for line in preview_data["lines"]:
            trade = submit_order(
                session,
                line["instrument_id"],
                line["side"],
                now_sh(),
                body.log.model_dump(),
                quantity=line["quantity"],
                source="build_target",
                checklist_json=confirmed,
            )
            created.append({"id": trade.id, "status": trade.status, "reject_reason": trade.reject_reason})
    except DomainError as exc:
        session.rollback()
        raise fail(exc) from exc
    session.commit()
    return enc({"orders": created, "session_date": preview_data["session_date"]})


@router.post("/market/sync")
def sync(session: Session = Depends(get_session)):
    job = JobRun(kind="sync", status="running", started_at=now_sh(), message="")
    session.add(job)
    session.commit()
    try:
        result = sync_all(session)
        job.status = "succeeded"
        job.message = "\n".join(result["messages"])
    except Exception as exc:
        session.rollback()
        job = session.get(JobRun, job.id)
        job.status = "failed"
        job.message = str(exc)
        job.finished_at = now_sh()
        session.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job.finished_at = now_sh()
    session.commit()
    return enc({"messages": result["messages"]})


@router.post("/valuation/run")
def run_valuation(session: Session = Depends(get_session)):
    moment = now_sh()
    as_of = default_as_of(session, moment)
    account = the_account(session)
    if not account or as_of is None or as_of < account.created_on:
        raise HTTPException(status_code=400, detail="还没有已收盘的练习日")
    job = JobRun(kind="valuation", status="running", started_at=moment, message="")
    session.add(job)
    session.flush()
    try:
        filled = fill_pending(session, as_of, moment)
        reinvested = materialize_reinvest(session, as_of, moment)
        session.commit()
        run = create_snapshot_run(session, as_of, moment)
        job.status = "succeeded" if run.status == "succeeded" else run.status
        job.message = run.error or f"快照至 {as_of.isoformat()}，成交 {len(filled)} 笔，分红再投资 {len(reinvested)} 笔"
        job.finished_at = now_sh()
        session.commit()
    except DomainError as exc:
        session.rollback()
        failed = SnapshotRun(
            account_id=account.id,
            as_of=as_of,
            created_at=moment,
            status="failed",
            error=str(exc),
            is_current=False,
        )
        session.add(failed)
        session.commit()
        raise fail(exc) from exc
    return enc({"as_of": as_of, "run_id": run.id, "status": run.status, "filled": len(filled)})


@router.get("/dashboard")
def dashboard(session: Session = Depends(get_session)):
    moment = now_sh()
    estimate = estimate_view(session, moment)
    run = current_run(session)
    series = []
    if run and run.status == "succeeded":
        snaps = session.scalars(
            select(DailySnapshot).where(DailySnapshot.run_id == run.id).order_by(DailySnapshot.trade_date)
        ).all()
        series = [
            {
                "date": snap.trade_date,
                "nav": snap.nav,
                "benchmark_nav": snap.benchmark_nav,
                "total_value": snap.total_value,
                "daily_pnl": snap.daily_pnl,
                "daily_return": snap.daily_return,
                "cumulative_return": snap.cumulative_return,
                "cash": snap.cash,
            }
            for snap in snaps
        ]
    latest = series[-1] if series else None
    metrics = None
    if series:
        metrics = risk_metrics(
            [item["nav"] for item in series],
            [item["daily_return"] for item in series],
            risk_free_rate(session),
        )
    return enc(
        {
            "estimate": estimate,
            "snapshot": None
            if not run
            else {"id": run.id, "as_of": run.as_of, "status": run.status, "error": run.error, "created_at": run.created_at},
            "latest": latest,
            "series": [
                {key: value for key, value in item.items() if key != "daily_return"}
                for item in series
            ],
            "metrics": metrics,
            "metric_notes": {
                "volatility": "日收益样本标准差 × √252。不足 20 个交易日不显示。",
                "max_drawdown": "净值相对历史峰值的最大跌幅。",
                "sharpe": "（年化收益 − 无风险利率）/ 年化波动。无风险利率默认 0，可在设置里改。",
                "calmar": "年化收益 / 最大回撤的绝对值。没有回撤时不显示。",
            },
        }
    )


@router.get("/logs")
def logs(session: Session = Depends(get_session)):
    rows = session.scalars(
        select(Trade).options(selectinload(Trade.instrument), selectinload(Trade.log)).order_by(Trade.id.desc())
    ).all()
    today = now_sh().date()
    payload = []
    for row in rows:
        if not row.log:
            continue
        item = _trade_dict(row)
        item["outcome"] = trade_outcome(session, row, today)
        payload.append(item)
    return enc(payload)


@router.get("/jobs")
def jobs(session: Session = Depends(get_session)):
    rows = session.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(8)).all()
    return enc(
        [
            {"id": row.id, "kind": row.kind, "status": row.status, "message": row.message, "started_at": row.started_at}
            for row in rows
        ]
    )


@router.get("/export/{kind}")
def export(kind: str, session: Session = Depends(get_session)):
    if kind == "plans.json":
        plans = session.scalars(select(AllocationPlan)).all()
        import json

        payload = enc([_plan_dict(session, plan) for plan in plans])
        versions = session.scalars(select(PlanWeightVersion).options(selectinload(PlanWeightVersion.assets).selectinload(PlanAsset.instrument))).all()
        payload = {
            "plans": payload,
            "versions": [
                {
                    "plan_id": version.plan_id,
                    "effective_on": version.effective_on.isoformat(),
                    "assets": [
                        {
                            "symbol": asset.instrument.symbol if asset.instrument else "CASH",
                            "asset_class": asset.asset_class,
                            "target_weight": format(asset.target_weight, "f"),
                        }
                        for asset in version.assets
                    ],
                }
                for version in versions
            ],
        }
        return Response(json.dumps(enc(payload), ensure_ascii=False, indent=2), media_type="application/json")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if kind == "trades.csv":
        writer.writerow(["id", "date", "symbol", "name", "side", "status", "quantity", "price", "amount", "reason", "temporary"])
        rows = session.scalars(select(Trade).options(selectinload(Trade.instrument), selectinload(Trade.log)).order_by(Trade.id)).all()
        for row in rows:
            writer.writerow(
                [
                    row.id,
                    row.trade_date,
                    row.instrument.symbol,
                    row.instrument.name,
                    row.side,
                    row.status,
                    row.quantity or row.quantity_requested or "",
                    row.price or "",
                    row.amount or row.amount_requested or "",
                    row.log.reason if row.log else "",
                    "yes" if row.is_temporary else "no",
                ]
            )
    elif kind == "snapshots.csv":
        run = current_run(session)
        writer.writerow(["date", "total", "cash", "market", "net_flow", "daily_pnl", "nav", "benchmark_nav", "cumulative_return"])
        if run:
            snaps = session.scalars(select(DailySnapshot).where(DailySnapshot.run_id == run.id).order_by(DailySnapshot.trade_date)).all()
            for snap in snaps:
                writer.writerow(
                    [
                        snap.trade_date,
                        snap.total_value,
                        snap.cash,
                        snap.market_value,
                        snap.net_flow,
                        snap.daily_pnl,
                        snap.nav,
                        snap.benchmark_nav or "",
                        snap.cumulative_return,
                    ]
                )
    elif kind == "logs.csv":
        writer.writerow(["trade_id", "created_at", "skipped", "reason", "hypothesis", "exit_condition", "confidence", "emotion"])
        rows = session.scalars(select(DecisionLog).order_by(DecisionLog.id)).all()
        trades = {trade.decision_log_id: trade.id for trade in session.scalars(select(Trade))}
        for row in rows:
            writer.writerow(
                [trades.get(row.id, ""), row.created_at, row.skipped, row.reason, row.hypothesis, row.exit_condition, row.confidence or "", row.emotion_tag]
            )
    else:
        raise HTTPException(status_code=404, detail="未知导出类型")
    return Response(buffer.getvalue().encode("utf-8-sig"), media_type="text/csv")


class SettingsIn(BaseModel):
    fee_rate: Decimal
    risk_free_rate: Decimal
    dividend_reinvest: bool = False


class IgnoreIn(BaseModel):
    reason: str


class ExecuteIn(BaseModel):
    log: LogIn = Field(default_factory=LogIn)
    checklist: list[str] = Field(default_factory=list)


class BarIn(BaseModel):
    instrument_id: int
    trade_date: date
    close: Decimal


class FxIn(BaseModel):
    currency: str
    trade_date: date
    rate: Decimal


class ClassBenchIn(BaseModel):
    asset_class: str
    instrument_id: int


class BacktestIn(BaseModel):
    name: str = "草稿回测"
    start: date
    end: date
    frequency: str = "monthly"
    assets: list[WeightIn]


def _task_dict(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "asset_class": row.asset_class,
        "instrument_id": row.instrument_id,
        "opened_on": row.opened_on,
        "deviation": row.deviation,
        "suggested_amount": row.suggested_amount,
        "status": row.status,
        "ignore_reason": row.ignore_reason,
        "extra_threshold": row.extra_threshold,
    }


@router.get("/settings")
def get_settings(session: Session = Depends(get_session)):
    return enc(read_settings(session))


@router.put("/settings")
def put_settings(body: SettingsIn, session: Session = Depends(get_session)):
    try:
        payload = save_settings(session, body.fee_rate, body.risk_free_rate, body.dividend_reinvest, now_sh())
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return enc(payload)


@router.post("/class-benchmarks")
def class_benchmark(body: ClassBenchIn, session: Session = Depends(get_session)):
    try:
        set_class_benchmark(session, body.asset_class, body.instrument_id)
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return enc(read_settings(session))


@router.post("/market/bars")
def manual_bar(body: BarIn, session: Session = Depends(get_session)):
    try:
        bar = repair_bar(session, body.instrument_id, body.trade_date, body.close)
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return enc({"instrument_id": bar.instrument_id, "trade_date": bar.trade_date, "close": bar.close, "source": bar.source})


@router.post("/fx")
def fx(body: FxIn, session: Session = Depends(get_session)):
    try:
        row = save_fx(session, body.currency, body.trade_date, body.rate)
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return enc({"currency": row.currency, "trade_date": row.trade_date, "rate": row.rate})


@router.get("/attribution")
def attribution(start: date, end: date, session: Session = Depends(get_session)):
    try:
        return enc(brinson(session, start, end))
    except DomainError as exc:
        raise fail(exc) from exc


@router.get("/attribution.csv")
def attribution_csv(start: date, end: date, session: Session = Depends(get_session)):
    try:
        payload = brinson(session, start, end)
    except DomainError as exc:
        raise fail(exc) from exc
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["asset_class", "actual_weight", "target_weight", "actual_return", "benchmark_return", "allocation", "selection"])
    for row in payload["rows"]:
        writer.writerow([row["asset_class"], row["actual_weight"], row["target_weight"], row["actual_return"], row["benchmark_return"], row["allocation"], row["selection"]])
    writer.writerow(["excess", payload["excess"], "allocation", payload["allocation"], "selection", payload["selection"], "residual", payload["residual"]])
    return Response(buffer.getvalue().encode("utf-8-sig"), media_type="text/csv")


@router.get("/rebalance/tasks")
def rebalance_tasks(session: Session = Depends(get_session)):
    rows = refresh_tasks(session, now_sh())
    session.commit()
    return enc({"stats": task_stats(rows), "tasks": [_task_dict(row) for row in rows]})


@router.post("/rebalance/tasks/{task_id}/ignore")
def rebalance_ignore(task_id: int, body: IgnoreIn, session: Session = Depends(get_session)):
    try:
        task = ignore_task(session, task_id, body.reason)
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return enc(_task_dict(task))


@router.post("/rebalance/tasks/{task_id}/execute")
def rebalance_execute(task_id: int, body: ExecuteIn, session: Session = Depends(get_session)):
    try:
        confirmed = confirm_checklist(session, body.checklist)
        task = execute_task(session, task_id, now_sh(), body.log.model_dump(), checklist_json=confirmed)
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return enc(_task_dict(task))


@router.post("/backtests")
def backtests(body: BacktestIn, session: Session = Depends(get_session)):
    try:
        payload = run_backtest(
            session,
            body.name,
            body.start,
            body.end,
            [item.model_dump() for item in body.assets],
            body.frequency,
            now_sh(),
        )
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return enc(payload)


@router.get("/backup")
def download_backup():
    return FileResponse(DB_PATH, filename=f"sandbox-{date.today().isoformat()}.db")


@router.post("/backup/restore")
async def restore_backup(confirm: str = Form(""), file: UploadFile = File(...)):
    if confirm != "恢复":
        raise HTTPException(status_code=400, detail="请输入“恢复”以确认覆盖当前练习账本")
    data = await file.read()
    if not data.startswith(b"SQLite format 3"):
        raise HTTPException(status_code=400, detail="这不是数据库文件")
    incoming_path = DB_PATH.with_suffix(".incoming")
    safety = DB_PATH.with_name(f"sandbox-before-restore-{date.today().isoformat()}.db")
    incoming_path.write_bytes(data)
    shutil.copy2(DB_PATH, safety)
    incoming = sqlite3.connect(incoming_path)
    live = sqlite3.connect(DB_PATH)
    try:
        incoming.backup(live)
    finally:
        incoming.close()
        live.close()
        incoming_path.unlink(missing_ok=True)
    return {"ok": True, "saved_previous": safety.name}


class ChecklistIn(BaseModel):
    items: list[str]


class PasswordIn(BaseModel):
    password: str
    current: str = ""


class LoginIn(BaseModel):
    password: str


class StressIn(BaseModel):
    start: date
    end: date
    name: str = "自定义情景"


@router.get("/checklist")
def get_checklist(session: Session = Depends(get_session)):
    return {"items": checklist_items(session)}


@router.put("/checklist")
def put_checklist(body: ChecklistIn, session: Session = Depends(get_session)):
    try:
        items = save_checklist(session, body.items)
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return {"items": items}


@router.get("/reports")
def reports(period: str, anchor: date, session: Session = Depends(get_session)):
    try:
        return enc(period_report(session, period, anchor))
    except DomainError as exc:
        raise fail(exc) from exc


@router.get("/plans/compare")
def plan_compare(left: int, right: int, session: Session = Depends(get_session)):
    try:
        return enc(compare_plans(session, left, right, now_sh().date()))
    except DomainError as exc:
        raise fail(exc) from exc


@router.get("/calendar")
def calendar(year: int, month: int, session: Session = Depends(get_session)):
    try:
        return enc(month_calendar(session, year, month))
    except DomainError as exc:
        raise fail(exc) from exc


@router.get("/stress/scenarios")
def scenarios():
    return {"scenarios": stress_catalog()}


@router.post("/stress")
def stress(body: StressIn, session: Session = Depends(get_session)):
    try:
        payload = apply_stress(session, body.start, body.end, body.name)
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return enc(payload)


@router.get("/auth/status")
def status(session: Session = Depends(get_session)):
    return auth_status(session)


@router.post("/auth/login")
def auth_login(body: LoginIn, session: Session = Depends(get_session)):
    try:
        token = login(session, body.password)
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return {"token": token}


@router.post("/auth/password")
def auth_password(body: PasswordIn, session: Session = Depends(get_session)):
    try:
        if body.password:
            set_password(session, body.password, body.current)
        else:
            clear_password(session, body.current)
    except DomainError as exc:
        raise fail(exc) from exc
    session.commit()
    return auth_status(session)
