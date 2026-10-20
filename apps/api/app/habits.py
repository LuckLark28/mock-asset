"""周报月报、方案对照、交易前清单、日历、情景压力和本机访问密码。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.analytics import brinson
from app.domain import (
    ZERO,
    DomainError,
    estimate_view,
    latest_close,
    now_sh,
    the_account,
    weight_version_on,
)
from app.models import (
    AllocationPlan,
    ClassBenchmark,
    CorporateAction,
    DailySnapshot,
    Instrument,
    RebalanceTask,
    Setting,
    Trade,
)

DEFAULT_CHECKLIST = [
    {"id": "direction", "text": "这笔交易符合当前方案，或我清楚它偏离了方案"},
    {"id": "capacity", "text": "数量是整手，现金或可卖数量足够"},
    {"id": "reason", "text": "我写了决策理由，或确认跳过并标为临时决策"},
    {"id": "price", "text": "我知道成交价是成交日收盘价，不是现在看到的价格"},
]

SCENARIOS = [
    {"id": "crash-2015", "name": "2015 年股灾", "start": "2015-06-12", "end": "2015-08-26"},
    {"id": "delever-2018", "name": "2018 年去杠杆", "start": "2018-01-26", "end": "2018-12-28"},
    {"id": "covid-2020", "name": "2020 年疫情", "start": "2020-01-20", "end": "2020-03-23"},
    {"id": "rates-2022", "name": "2022 年利率上行", "start": "2022-01-04", "end": "2022-10-31"},
]

STRESS_NOTE = "这是把历史区间的涨跌按当前持仓重放，不考虑当时的调仓、流动性和分红。未覆盖的持仓按原市值保留。历史情景不代表未来。"


def _setting(session: Session, key: str) -> str | None:
    row = session.get(Setting, key)
    return row.value if row else None


def _put_setting(session: Session, key: str, value: str | None) -> None:
    row = session.get(Setting, key)
    if value is None:
        if row:
            session.delete(row)
        return
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))


def checklist_items(session: Session) -> list[dict]:
    raw = _setting(session, "checklist_json")
    if not raw:
        return list(DEFAULT_CHECKLIST)
    parsed = json.loads(raw)
    return [{"id": item["id"], "text": item["text"]} for item in parsed if item.get("text")]


def save_checklist(session: Session, texts: list[str]) -> list[dict]:
    items = []
    for index, text in enumerate(texts, start=1):
        cleaned = text.strip()
        if cleaned:
            items.append({"id": f"item-{index}", "text": cleaned})
    if not items:
        raise DomainError("至少保留一条交易前检查")
    _put_setting(session, "checklist_json", json.dumps(items, ensure_ascii=False))
    session.flush()
    return items


def confirm_checklist(session: Session, checked: list[str]) -> str:
    items = checklist_items(session)
    missing = [item["text"] for item in items if item["id"] not in set(checked)]
    if missing:
        raise DomainError("请先勾选全部交易前检查")
    return json.dumps([item["text"] for item in items], ensure_ascii=False)


def _bounds(kind: str, anchor: date) -> tuple[date, date]:
    if kind == "week":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)
    if kind == "month":
        last = monthrange(anchor.year, anchor.month)[1]
        return date(anchor.year, anchor.month, 1), date(anchor.year, anchor.month, last)
    raise DomainError("报告只能按周或按月")


def period_report(session: Session, kind: str, anchor: date) -> dict:
    from app.domain import current_run

    start, end = _bounds(kind, anchor)
    run = current_run(session)
    snaps = []
    if run and run.status == "succeeded":
        snaps = session.scalars(
            select(DailySnapshot)
            .where(DailySnapshot.run_id == run.id, DailySnapshot.trade_date >= start, DailySnapshot.trade_date <= end)
            .order_by(DailySnapshot.trade_date)
        ).all()
    portfolio = benchmark = excess = drawdown = None
    if len(snaps) >= 2 and snaps[0].nav:
        portfolio = snaps[-1].nav / snaps[0].nav - 1
        if snaps[0].benchmark_nav and snaps[-1].benchmark_nav:
            benchmark = snaps[-1].benchmark_nav / snaps[0].benchmark_nav - 1
            excess = portfolio - benchmark
        peak = snaps[0].nav
        worst = ZERO
        for snap in snaps:
            peak = max(peak, snap.nav)
            if peak:
                worst = min(worst, snap.nav / peak - 1)
        drawdown = worst
    trades = session.scalars(
        select(Trade)
        .where(Trade.trade_date >= start, Trade.trade_date <= end)
        .options(selectinload(Trade.instrument), selectinload(Trade.log))
        .order_by(Trade.id)
    ).all()
    tasks = session.scalars(select(RebalanceTask).where(RebalanceTask.opened_on >= start, RebalanceTask.opened_on <= end)).all()
    attribution = None
    if len(snaps) >= 2:
        try:
            attribution = brinson(session, snaps[0].trade_date, snaps[-1].trade_date)
        except DomainError:
            attribution = None
    return {
        "period": kind,
        "start": start,
        "end": end,
        "portfolio_return": portfolio,
        "benchmark_return": benchmark,
        "excess": excess,
        "max_drawdown": drawdown,
        "snapshot_days": len(snaps),
        "trades": [
            {
                "date": trade.trade_date,
                "name": trade.instrument.name,
                "side": trade.side,
                "status": trade.status,
                "reason": trade.log.reason if trade.log else "",
                "temporary": trade.is_temporary,
            }
            for trade in trades
        ],
        "rebalance": {
            "opened": len(tasks),
            "executed": sum(1 for task in tasks if task.status == "executed"),
            "ignored": sum(1 for task in tasks if task.status == "ignored"),
        },
        "attribution": attribution,
        "note": "收益用区间内第一笔和最后一笔快照的净值计算。快照不足两天时，只回顾交易和再平衡。",
    }


def compare_plans(session: Session, left_id: int, right_id: int, day: date) -> dict:
    if left_id == right_id:
        raise DomainError("请选择两套不同的方案")
    left = session.get(AllocationPlan, left_id)
    right = session.get(AllocationPlan, right_id)
    if not left or not right:
        raise DomainError("方案不存在")
    left_version = weight_version_on(session, left.id, day)
    right_version = weight_version_on(session, right.id, day)
    if not left_version or not right_version:
        raise DomainError("方案还没有权重")
    names: dict[tuple, str] = {}
    classes: dict[tuple, str] = {}
    left_weights: dict[tuple, Decimal] = {}
    right_weights: dict[tuple, Decimal] = {}
    for asset in left_version.assets:
        key = (asset.instrument_id, asset.asset_class)
        left_weights[key] = asset.target_weight
        classes[key] = asset.asset_class
        instrument = session.get(Instrument, asset.instrument_id) if asset.instrument_id else None
        names[key] = instrument.name if instrument else "现金"
    for asset in right_version.assets:
        key = (asset.instrument_id, asset.asset_class)
        right_weights[key] = asset.target_weight
        classes[key] = asset.asset_class
        instrument = session.get(Instrument, asset.instrument_id) if asset.instrument_id else None
        names[key] = instrument.name if instrument else "现金"
    rows = []
    for key in sorted(set(left_weights) | set(right_weights), key=lambda item: (item[1], names.get(item, ""))):
        before = left_weights.get(key, ZERO)
        after = right_weights.get(key, ZERO)
        rows.append(
            {
                "name": names[key],
                "asset_class": classes[key],
                "left_weight": before,
                "right_weight": after,
                "delta": after - before,
            }
        )
    return {
        "left": {"id": left.id, "name": left.name, "primary": left.is_primary},
        "right": {"id": right.id, "name": right.name, "primary": right.is_primary},
        "rows": rows,
        "note": "对照的是两套方案在当天生效的目标权重，不会切换主账户正在使用的方案。",
    }


def month_calendar(session: Session, year: int, month: int) -> dict:
    from app.domain import current_run, is_trading_day

    if month < 1 or month > 12:
        raise DomainError("月份无效")
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    events: dict[date, list[dict]] = {}

    def add(day: date, kind: str, text: str) -> None:
        events.setdefault(day, []).append({"kind": kind, "text": text})

    trades = session.scalars(
        select(Trade).where(Trade.trade_date >= start, Trade.trade_date <= end).options(selectinload(Trade.instrument))
    ).all()
    for trade in trades:
        side = "买入" if trade.side == "buy" else "卖出"
        status = {"pending": "待成交", "filled": "已成交", "rejected": "已拒绝", "cancelled": "已撤销"}.get(trade.status, trade.status)
        add(trade.trade_date, "trade", f"{side} {trade.instrument.name} · {status}")
    account = the_account(session)
    held: set[int] = set()
    if account:
        view = estimate_view(session, now_sh())
        for row in view.get("weights") or []:
            if row.get("instrument_id"):
                held.add(row["instrument_id"])
    if held:
        actions = session.scalars(
            select(CorporateAction).where(CorporateAction.ex_date >= start, CorporateAction.ex_date <= end, CorporateAction.instrument_id.in_(held))
        ).all()
        for action in actions:
            instrument = session.get(Instrument, action.instrument_id)
            name = instrument.name if instrument else "标的"
            if action.cash_per_share and action.cash_per_share > 0:
                add(action.ex_date, "dividend", f"{name} 除息")
            if action.split_ratio and action.split_ratio != 1:
                add(action.ex_date, "split", f"{name} 份额折算")
    run = current_run(session)
    if run and run.status == "succeeded":
        days = session.scalars(
            select(DailySnapshot.trade_date).where(
                DailySnapshot.run_id == run.id,
                DailySnapshot.trade_date >= start,
                DailySnapshot.trade_date <= end,
            )
        ).all()
        for day in days:
            add(day, "snapshot", "已有快照")
    tasks = session.scalars(select(RebalanceTask).where(RebalanceTask.opened_on >= start, RebalanceTask.opened_on <= end)).all()
    for task in tasks:
        add(task.opened_on, "rebalance", f"{task.name} 偏离提醒")
    days = []
    cursor = start
    while cursor <= end:
        days.append({"date": cursor, "weekday": cursor.weekday(), "trading": is_trading_day(session, cursor), "events": events.get(cursor, [])})
        cursor += timedelta(days=1)
    return {"year": year, "month": month, "days": days}


def stress_catalog() -> list[dict]:
    return list(SCENARIOS)


def apply_stress(session: Session, start: date, end: date, label: str) -> dict:
    if end < start:
        raise DomainError("结束日需要不早于开始日")
    before_trades = session.scalar(select(func.count(Trade.id))) or 0
    before_snaps = session.scalar(select(func.count(DailySnapshot.id))) or 0
    view = estimate_view(session, now_sh())
    if not view.get("total"):
        raise DomainError("请先建立练习账户")
    rows = []
    stressed_total = ZERO
    for weight in view["weights"]:
        current = weight["market_value"]
        instrument_id = weight.get("instrument_id")
        if instrument_id is None:
            rows.append({**_stress_row(weight["name"], current, ZERO, "现金不产生收益", True)})
            stressed_total += current
            continue
        shock, covered, source = _shock(session, instrument_id, weight["asset_class"], start, end)
        shocked = current * (1 + shock) if covered else current
        rows.append(_stress_row(weight["name"], current, shock, source, covered))
        stressed_total += shocked
    after_trades = session.scalar(select(func.count(Trade.id))) or 0
    after_snaps = session.scalar(select(func.count(DailySnapshot.id))) or 0
    if after_trades != before_trades or after_snaps != before_snaps:
        raise DomainError("压力测试不应改动主账户")
    current_total = view["total"]
    return {
        "name": label or "自定义情景",
        "start": start,
        "end": end,
        "current_total": current_total,
        "stressed_total": stressed_total,
        "change": stressed_total / current_total - 1 if current_total else ZERO,
        "rows": rows,
        "disclaimer": STRESS_NOTE,
    }


def _shock(session: Session, instrument_id: int, asset_class: str, start: date, end: date) -> tuple[Decimal, bool, str]:
    own = _span_return(session, instrument_id, start, end)
    if own is not None:
        return own, True, "持仓标的"
    mapping = session.get(ClassBenchmark, asset_class)
    if mapping:
        proxy = _span_return(session, mapping.instrument_id, start, end)
        if proxy is not None:
            return proxy, True, "类别基准"
    return ZERO, False, "行情未覆盖"


def _span_return(session: Session, instrument_id: int, start: date, end: date) -> Decimal | None:
    begin = latest_close(session, instrument_id, start)
    finish = latest_close(session, instrument_id, end)
    if begin and finish and begin[0] < finish[0] and begin[1]:
        return finish[1] / begin[1] - 1
    return None


def _stress_row(name: str, current: Decimal, shock: Decimal, source: str, covered: bool) -> dict:
    return {
        "name": name,
        "current": current,
        "shock": shock,
        "stressed": current * (1 + shock) if covered else current,
        "source": source,
        "covered": covered,
    }


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{salt.hex()}${digest.hex()}"


def _password_matches(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$", 1)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def auth_status(session: Session) -> dict:
    return {"enabled": _setting(session, "access_password") is not None}


def set_password(session: Session, password: str, current: str = "") -> None:
    stored = _setting(session, "access_password")
    if stored and not _password_matches(current, stored):
        raise DomainError("当前密码不正确")
    if len(password) < 4:
        raise DomainError("访问密码至少 4 位")
    _put_setting(session, "access_password", _hash_password(password))
    _put_setting(session, "access_token", None)
    session.flush()


def clear_password(session: Session, current: str) -> None:
    stored = _setting(session, "access_password")
    if not stored:
        return
    if not _password_matches(current, stored):
        raise DomainError("当前密码不正确")
    _put_setting(session, "access_password", None)
    _put_setting(session, "access_token", None)
    session.flush()


def login(session: Session, password: str) -> str:
    stored = _setting(session, "access_password")
    if not stored:
        raise DomainError("还没有设置访问密码")
    if not _password_matches(password, stored):
        raise DomainError("密码不正确")
    token = secrets.token_urlsafe(24)
    _put_setting(session, "access_token", hashlib.sha256(token.encode()).hexdigest())
    session.flush()
    return token


def access_granted(session: Session, token: str | None) -> bool:
    if _setting(session, "access_password") is None:
        return True
    if not token:
        return False
    stored = _setting(session, "access_token")
    digest = hashlib.sha256(token.encode()).hexdigest()
    return bool(stored) and hmac.compare_digest(stored, digest)
