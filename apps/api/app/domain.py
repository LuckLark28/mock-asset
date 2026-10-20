"""账本投影、成交、净值与快照。金额一律用 Decimal。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Account,
    AllocationPlan,
    Benchmark,
    CashFlow,
    CorporateAction,
    DailySnapshot,
    DecisionLog,
    Instrument,
    MarketBar,
    PlanAsset,
    PlanBinding,
    PlanWeightVersion,
    Setting,
    SnapshotLine,
    SnapshotRun,
    Trade,
    TradingDay,
    FxRate,
)

SH = ZoneInfo("Asia/Shanghai")
CENT = Decimal("0.01")
ZERO = Decimal("0")
ONE = Decimal("1")
LOT = 100
EMOTIONS = {"", "平静", "焦虑", "兴奋", "不甘", "恐惧"}
CLASSES = {"equity", "bond", "commodity", "cash"}


class DomainError(ValueError):
    pass


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def now_sh() -> datetime:
    return datetime.now(SH)


def d(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value))


@dataclass
class Pos:
    qty: int = 0
    avg_cost: Decimal = ZERO
    unsettled_qty: int = 0
    unsettled_on: date | None = None


@dataclass
class State:
    cash: Decimal = ZERO
    positions: dict[int, Pos] = field(default_factory=dict)

    def settle(self, day: date) -> None:
        for pos in self.positions.values():
            if pos.unsettled_on is not None and day > pos.unsettled_on:
                pos.unsettled_qty = 0
                pos.unsettled_on = None

    def sellable(self, instrument_id: int) -> int:
        pos = self.positions.get(instrument_id)
        if not pos:
            return 0
        return pos.qty - pos.unsettled_qty


def calendar_loaded(session: Session) -> bool:
    return session.scalar(select(func.count()).select_from(TradingDay)) > 0


def is_trading_day(session: Session, day: date) -> bool:
    if calendar_loaded(session):
        return session.get(TradingDay, day) is not None
    return day.weekday() < 5


def next_trading_day(session: Session, day: date) -> date:
    if calendar_loaded(session):
        found = session.scalar(
            select(TradingDay.trade_date)
            .where(TradingDay.trade_date > day)
            .order_by(TradingDay.trade_date)
            .limit(1)
        )
        if found:
            return found
    cursor = day + timedelta(days=1)
    for _ in range(14):
        if is_trading_day(session, cursor):
            return cursor
        cursor += timedelta(days=1)
    raise DomainError("找不到下一个交易日")


def previous_trading_day(session: Session, day: date) -> date | None:
    if calendar_loaded(session):
        return session.scalar(
            select(func.max(TradingDay.trade_date)).where(TradingDay.trade_date < day)
        )
    cursor = day - timedelta(days=1)
    for _ in range(14):
        if is_trading_day(session, cursor):
            return cursor
        cursor -= timedelta(days=1)
    return None


def trading_days_between(session: Session, start: date, end: date) -> list[date]:
    if end < start:
        return []
    if calendar_loaded(session):
        rows = session.scalars(
            select(TradingDay.trade_date)
            .where(TradingDay.trade_date >= start, TradingDay.trade_date <= end)
            .order_by(TradingDay.trade_date)
        ).all()
        return list(rows)
    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def submission_session(session: Session, moment: datetime) -> date:
    local = moment.astimezone(SH)
    day = local.date()
    if is_trading_day(session, day) and local.time() < time(15, 0):
        return day
    return next_trading_day(session, day)


def default_as_of(session: Session, moment: datetime) -> date | None:
    local = moment.astimezone(SH)
    day = local.date()
    if is_trading_day(session, day) and local.time() >= time(15, 0):
        return day
    return previous_trading_day(session, day)


def the_account(session: Session) -> Account | None:
    return session.scalar(select(Account).limit(1))


def active_plan(session: Session) -> AllocationPlan | None:
    return session.scalar(select(AllocationPlan).where(AllocationPlan.is_primary.is_(True)))


def binding_on(session: Session, account_id: int, day: date) -> PlanBinding | None:
    return session.scalar(
        select(PlanBinding)
        .where(PlanBinding.account_id == account_id, PlanBinding.effective_on <= day)
        .order_by(PlanBinding.effective_on.desc(), PlanBinding.id.desc())
        .limit(1)
    )


def weight_version_on(session: Session, plan_id: int, day: date) -> PlanWeightVersion | None:
    return session.scalar(
        select(PlanWeightVersion)
        .where(PlanWeightVersion.plan_id == plan_id, PlanWeightVersion.effective_on <= day)
        .order_by(PlanWeightVersion.effective_on.desc(), PlanWeightVersion.id.desc())
        .options(selectinload(PlanWeightVersion.assets).selectinload(PlanAsset.instrument))
        .limit(1)
    )


def targets_on(session: Session, account_id: int, day: date) -> tuple[AllocationPlan | None, list[PlanAsset]]:
    binding = binding_on(session, account_id, day)
    if not binding:
        return None, []
    plan = session.get(AllocationPlan, binding.plan_id)
    version = weight_version_on(session, binding.plan_id, day)
    if not plan or not version:
        return plan, []
    return plan, list(version.assets)


def latest_close(session: Session, instrument_id: int, on_or_before: date) -> tuple[date, Decimal] | None:
    row = session.scalar(
        select(MarketBar)
        .where(MarketBar.instrument_id == instrument_id, MarketBar.trade_date <= on_or_before)
        .order_by(MarketBar.trade_date.desc())
        .limit(1)
    )
    if not row:
        return None
    return row.trade_date, row.close


def bar_on(session: Session, instrument_id: int, day: date) -> MarketBar | None:
    return session.scalar(
        select(MarketBar).where(MarketBar.instrument_id == instrument_id, MarketBar.trade_date == day)
    )


def project(session: Session, account_id: int, through: date, before_trade_id: int | None = None) -> State:
    """把出入金、分红和已成交交易重放到 through 日收盘。before_trade_id 排除该笔及之后的成交。"""
    cashflows = session.scalars(
        select(CashFlow).where(CashFlow.account_id == account_id, CashFlow.occurred_on <= through).order_by(CashFlow.occurred_on, CashFlow.id)
    ).all()
    trade_stmt = select(Trade).where(
        Trade.account_id == account_id,
        Trade.status == "filled",
        Trade.trade_date <= through,
    )
    if before_trade_id is not None:
        trade_stmt = trade_stmt.where(Trade.id < before_trade_id)
    trades = session.scalars(trade_stmt.order_by(Trade.trade_date, Trade.id)).all()
    instrument_ids = {trade.instrument_id for trade in trades}
    dividends = []
    if instrument_ids:
        dividends = session.scalars(
            select(CorporateAction).where(
                CorporateAction.instrument_id.in_(instrument_ids),
                CorporateAction.ex_date <= through,
            )
        ).all()

    days = sorted(
        {item.occurred_on for item in cashflows}
        | {item.trade_date for item in trades}
        | {item.ex_date for item in dividends}
    )
    state = State()
    end_qty: dict[date, dict[int, int]] = {}
    for day in days:
        state.settle(day)
        for action in dividends:
            if action.ex_date != day:
                continue
            record = action.record_date
            if record is None or record not in end_qty:
                qty = state.positions.get(action.instrument_id, Pos()).qty
            else:
                qty = end_qty[record].get(action.instrument_id, 0)
            instrument = session.get(Instrument, action.instrument_id)
            fx = fx_on(session, instrument.currency if instrument else "CNY", day)
            if action.cash_per_share:
                state.cash += money(d(qty) * action.cash_per_share * fx)
            ratio = action.split_ratio or ONE
            if ratio != ONE:
                pos = state.positions.setdefault(action.instrument_id, Pos())
                if pos.qty:
                    pos.qty = int((d(pos.qty) * ratio).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                    if pos.unsettled_qty:
                        pos.unsettled_qty = int((d(pos.unsettled_qty) * ratio).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                    pos.avg_cost = (pos.avg_cost / ratio).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if pos.qty else ZERO
        for flow in cashflows:
            if flow.occurred_on != day:
                continue
            if flow.direction == "in":
                state.cash += flow.amount
            else:
                if state.cash < flow.amount:
                    raise DomainError("现金不足，无法出金")
                state.cash -= flow.amount
        for trade in trades:
            if trade.trade_date != day:
                continue
            _apply_fill(state, trade)
        end_qty[day] = {instrument_id: pos.qty for instrument_id, pos in state.positions.items()}
    state.settle(through)
    return state


def _apply_fill(state: State, trade: Trade) -> None:
    if trade.quantity is None or trade.price is None or trade.amount is None:
        raise DomainError("成交记录缺少价格或数量")
    pos = state.positions.setdefault(trade.instrument_id, Pos())
    if trade.side == "buy":
        if state.cash < trade.amount:
            raise DomainError("现金不足")
        total_cost = pos.avg_cost * pos.qty + trade.amount
        pos.qty += trade.quantity
        pos.avg_cost = (total_cost / pos.qty).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if pos.qty else ZERO
        if pos.unsettled_on == trade.trade_date:
            pos.unsettled_qty += trade.quantity
        else:
            pos.unsettled_qty = trade.quantity
            pos.unsettled_on = trade.trade_date
        state.cash -= trade.amount
    elif trade.side == "sell":
        sellable = pos.qty - (pos.unsettled_qty if pos.unsettled_on == trade.trade_date else 0)
        if trade.quantity > sellable:
            raise DomainError("可卖数量不足")
        pos.qty -= trade.quantity
        if pos.qty == 0:
            pos.avg_cost = ZERO
        state.cash += trade.amount
    else:
        raise DomainError("未知买卖方向")


def create_account(session: Session, name: str, initial_cash: Decimal, plan_id: int, moment: datetime) -> Account:
    if the_account(session):
        raise DomainError("练习账户只有一个")
    plan = session.get(AllocationPlan, plan_id)
    if not plan or plan.status != "active":
        raise DomainError("方案不存在或已归档")
    if initial_cash <= 0:
        raise DomainError("初始资金需要大于 0")
    created = moment.astimezone(SH).date()
    account = Account(name=name.strip(), created_on=created, base_currency="CNY")
    session.add(account)
    session.flush()
    session.add(CashFlow(account_id=account.id, direction="in", amount=money(initial_cash), occurred_on=created, note="初始资金"))
    session.add(PlanBinding(account_id=account.id, plan_id=plan.id, effective_on=created))
    for item in session.scalars(select(AllocationPlan)).all():
        item.is_primary = item.id == plan.id
    session.flush()
    return account


def add_cashflow(session: Session, direction: str, amount: Decimal, moment: datetime, note: str) -> CashFlow:
    account = the_account(session)
    if not account:
        raise DomainError("请先建立练习账户")
    if direction not in {"in", "out"}:
        raise DomainError("方向只能是入金或出金")
    if amount <= 0:
        raise DomainError("金额需要大于 0")
    occurred = moment.astimezone(SH).date()
    amount = money(amount)
    if direction == "out":
        state = project(session, account.id, date(9999, 12, 31))
        if state.cash < amount:
            raise DomainError("现金不足，无法出金")
    flow = CashFlow(account_id=account.id, direction=direction, amount=amount, occurred_on=occurred, note=note.strip())
    session.add(flow)
    session.flush()
    return flow


def _log_from_payload(payload: dict, moment: datetime) -> DecisionLog:
    skip = bool(payload.get("skip"))
    if skip and not payload.get("confirm_skip"):
        raise DomainError("跳过决策日志需要确认")
    confidence = payload.get("confidence")
    emotion = (payload.get("emotion_tag") or "").strip()
    if emotion not in EMOTIONS:
        raise DomainError("情绪标签不在可选范围内")
    if confidence is not None and confidence not in {1, 2, 3, 4, 5}:
        raise DomainError("信心水平为 1 到 5")
    if not skip and not (payload.get("reason") or "").strip():
        raise DomainError("请填写决策理由，或确认跳过")
    log = DecisionLog(
        reason="" if skip else payload.get("reason", "").strip(),
        hypothesis="" if skip else (payload.get("hypothesis") or "").strip(),
        exit_condition="" if skip else (payload.get("exit_condition") or "").strip(),
        confidence=None if skip else confidence,
        emotion_tag="" if skip else emotion,
        skipped=skip,
        created_at=moment,
    )
    return log


def submit_order(
    session: Session,
    instrument_id: int,
    side: str,
    moment: datetime,
    log_payload: dict,
    quantity: int | None = None,
    amount: Decimal | None = None,
    source: str = "manual",
    checklist_json: str = "",
) -> Trade:
    account = the_account(session)
    if not account:
        raise DomainError("请先建立练习账户")
    plan = active_plan(session)
    if not plan:
        raise DomainError("请先设定生效方案")
    instrument = session.get(Instrument, instrument_id)
    if not instrument or not instrument.tradable:
        raise DomainError("标的不可交易")
    if side not in {"buy", "sell"}:
        raise DomainError("方向只能是买入或卖出")
    if (quantity is None) == (amount is None):
        raise DomainError("请填写数量或金额中的一项")
    if quantity is not None:
        if quantity <= 0 or quantity % instrument.lot_size != 0:
            raise DomainError(f"数量需要是 {instrument.lot_size} 股的整数倍")
    if amount is not None and amount <= 0:
        raise DomainError("金额需要大于 0")
    trade_date = submission_session(session, moment)
    log = _log_from_payload(log_payload, moment)
    session.add(log)
    session.flush()
    trade = Trade(
        account_id=account.id,
        plan_id=plan.id,
        instrument_id=instrument.id,
        side=side,
        quantity_requested=quantity,
        amount_requested=money(amount) if amount is not None else None,
        status="pending",
        trade_date=trade_date,
        submitted_at=moment,
        decision_log_id=log.id,
        is_temporary=log.skipped,
        source=source,
        checklist_json=checklist_json,
    )
    session.add(trade)
    session.flush()
    _precheck(session, account.id, trade)
    return trade


def _precheck(session: Session, account_id: int, trade: Trade) -> None:
    quote = latest_close(session, trade.instrument_id, trade.trade_date)
    if not quote or trade.quantity_requested is None:
        return
    _, price = quote
    state = project(session, account_id, trade.trade_date, before_trade_id=trade.id)
    instrument = session.get(Instrument, trade.instrument_id)
    fx = fx_on(session, instrument.currency if instrument else "CNY", trade.trade_date, strict=False) or ONE
    if trade.side == "buy" and money(price * trade.quantity_requested * fx * (ONE + fee_rate(session))) > state.cash:
        trade.status = "rejected"
        trade.reject_reason = "按最近收盘价估算，现金不足"
    if trade.side == "sell" and trade.quantity_requested > state.sellable(trade.instrument_id):
        trade.status = "rejected"
        trade.reject_reason = "可卖数量不足。当天买入的份额下一个交易日才能卖出"


def cancel_order(session: Session, trade_id: int) -> Trade:
    trade = session.get(Trade, trade_id)
    if not trade or trade.status != "pending":
        raise DomainError("只能撤销尚未成交的委托")
    trade.status = "cancelled"
    session.flush()
    return trade


def fill_pending(session: Session, as_of: date, moment: datetime) -> list[Trade]:
    """用对应交易日的收盘价成交。收盘价还没有时保持待成交。"""
    local = moment.astimezone(SH)
    orders = session.scalars(
        select(Trade)
        .where(Trade.status == "pending", Trade.trade_date <= as_of)
        .order_by(Trade.trade_date, Trade.id)
    ).all()
    touched: list[Trade] = []
    for trade in orders:
        if trade.trade_date == local.date() and local.time() < time(15, 0):
            continue
        bar = bar_on(session, trade.instrument_id, trade.trade_date)
        if not bar:
            continue
        instrument = session.get(Instrument, trade.instrument_id)
        price = bar.close
        instrument_fx = fx_on(session, instrument.currency, trade.trade_date, strict=False)
        if instrument_fx is None:
            continue
        state = project(session, trade.account_id, trade.trade_date, before_trade_id=trade.id)
        if trade.quantity_requested is not None:
            quantity = trade.quantity_requested
        else:
            unit = price * instrument_fx
            quantity = int((trade.amount_requested / unit) // instrument.lot_size) * instrument.lot_size
        if quantity <= 0:
            trade.status = "rejected"
            trade.reject_reason = "金额不足一手"
            touched.append(trade)
            session.flush()
            continue
        rate = fee_rate(session)
        gross = money(price * quantity * instrument_fx)
        fee = money(gross * rate)
        cost = gross + fee if trade.side == "buy" else gross - fee
        if trade.side == "buy" and cost > state.cash:
            trade.status = "rejected"
            trade.reject_reason = "现金不足，整笔拒绝"
        elif trade.side == "sell" and quantity > state.sellable(trade.instrument_id):
            trade.status = "rejected"
            trade.reject_reason = "可卖数量不足。当天买入的份额下一个交易日才能卖出"
        else:
            trade.status = "filled"
            trade.quantity = quantity
            trade.price = price
            trade.amount = cost
            trade.fee = fee
            trade.filled_at = moment
        touched.append(trade)
        session.flush()
    return touched


def build_target_preview(session: Session, moment: datetime) -> dict:
    account = the_account(session)
    plan = active_plan(session)
    if not account or not plan:
        raise DomainError("请先建立账户并设定生效方案")
    day = moment.astimezone(SH).date()
    state = project(session, account.id, date(9999, 12, 31))
    version = weight_version_on(session, plan.id, day)
    if not version:
        raise DomainError("方案还没有目标权重")
    prices: dict[int, Decimal] = {}
    market = ZERO
    for instrument_id, pos in state.positions.items():
        if pos.qty <= 0:
            continue
        quote = latest_close(session, instrument_id, day)
        if not quote:
            raise DomainError("有持仓缺少行情，请先同步")
        prices[instrument_id] = quote[1]
        market += money(quote[1] * pos.qty)
    total = state.cash + market
    lines = []
    for asset in version.assets:
        if asset.instrument_id is None:
            continue
        quote = latest_close(session, asset.instrument_id, day)
        if not quote:
            instrument = session.get(Instrument, asset.instrument_id)
            raise DomainError(f"{instrument.name if instrument else '标的'}还没有行情，请先同步")
        price = quote[1]
        prices[asset.instrument_id] = price
        current_qty = state.positions.get(asset.instrument_id, Pos()).qty
        current_value = money(price * current_qty)
        target_value = money(total * asset.target_weight)
        delta = target_value - current_value
        lot = session.get(Instrument, asset.instrument_id).lot_size
        quantity = int((abs(delta) / price) // lot) * lot
        if quantity <= 0:
            continue
        side = "buy" if delta > 0 else "sell"
        if side == "sell":
            quantity = min(quantity, state.sellable(asset.instrument_id))
            if quantity <= 0:
                continue
        lines.append(
            {
                "instrument_id": asset.instrument_id,
                "name": session.get(Instrument, asset.instrument_id).name,
                "symbol": session.get(Instrument, asset.instrument_id).symbol,
                "side": side,
                "quantity": quantity,
                "estimate_price": price,
                "estimate_amount": money(price * quantity),
            }
        )
    return {"total": total, "cash": state.cash, "session_date": submission_session(session, moment), "lines": lines}


def save_weight_version(session: Session, plan: AllocationPlan, assets: list[dict], effective_on: date, moment: datetime) -> PlanWeightVersion:
    if plan.status != "active":
        raise DomainError("归档方案不能修改权重")
    normalized: list[PlanAsset] = []
    total = ZERO
    seen: set[int | None] = set()
    for item in assets:
        weight = d(item["target_weight"])
        if weight < 0:
            raise DomainError("权重不能为负")
        asset_class = item["asset_class"]
        if asset_class not in CLASSES:
            raise DomainError("资产类别无效")
        instrument_id = item.get("instrument_id")
        if asset_class == "cash":
            instrument_id = None
        elif instrument_id is None:
            raise DomainError("非现金资产需要选择标的")
        if instrument_id in seen:
            raise DomainError("同一标的不能重复")
        seen.add(instrument_id)
        if instrument_id is not None:
            instrument = session.get(Instrument, instrument_id)
            if not instrument or not instrument.tradable:
                raise DomainError("标的不存在或不可交易")
            asset_class = instrument.asset_class
        total += weight
        normalized.append(PlanAsset(instrument_id=instrument_id, asset_class=asset_class, target_weight=weight))
    if abs(total - ONE) > Decimal("0.0001"):
        raise DomainError(f"目标权重合计需要为 100%，当前为 {total * 100:.2f}%")
    version = PlanWeightVersion(plan_id=plan.id, effective_on=effective_on, created_at=moment)
    session.add(version)
    session.flush()
    for asset in normalized:
        asset.version_id = version.id
        session.add(asset)
    session.flush()
    return version


def mark_to_market(session: Session, account: Account, day: date, state: State) -> tuple[Decimal, Decimal, list[dict]]:
    plan, assets = targets_on(session, account.id, day)
    target_by_instrument = {asset.instrument_id: asset.target_weight for asset in assets if asset.instrument_id}
    cash_target = sum((asset.target_weight for asset in assets if asset.asset_class == "cash"), ZERO)
    lines: list[dict] = []
    market = ZERO
    for instrument_id, pos in state.positions.items():
        if pos.qty <= 0:
            continue
        instrument = session.get(Instrument, instrument_id)
        exact = bar_on(session, instrument_id, day)
        if exact:
            price, flag = exact.close, "ok"
        else:
            prior = latest_close(session, instrument_id, day)
            if not prior:
                raise DomainError(f"{instrument.name} 没有可用价格，当日快照失败")
            price, flag = prior[1], "halted"
        fx = fx_on(session, instrument.currency, day)
        value = money(price * pos.qty * fx)
        market += value
        lines.append(
            {
                "instrument_id": instrument_id,
                "asset_class": instrument.asset_class,
                "name": instrument.name,
                "quantity": d(pos.qty),
                "close_price": price,
                "market_value": value,
                "target_weight": target_by_instrument.get(instrument_id, ZERO),
                "price_flag": flag,
            }
        )
    cash = money(state.cash)
    total = cash + market
    if total <= 0:
        raise DomainError("总资产需要大于 0")
    lines.append(
        {
            "instrument_id": None,
            "asset_class": "cash",
            "name": "现金",
            "quantity": cash,
            "close_price": None,
            "market_value": cash,
            "target_weight": cash_target,
            "price_flag": "ok",
        }
    )
    for line in lines:
        line["actual_weight"] = (line["market_value"] / total).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    held = {line["instrument_id"] for line in lines}
    for asset in assets:
        if asset.instrument_id and asset.instrument_id not in held and asset.target_weight > 0:
            instrument = session.get(Instrument, asset.instrument_id)
            lines.append(
                {
                    "instrument_id": asset.instrument_id,
                    "asset_class": asset.asset_class,
                    "name": instrument.name if instrument else "标的",
                    "quantity": ZERO,
                    "close_price": None,
                    "market_value": ZERO,
                    "actual_weight": ZERO,
                    "target_weight": asset.target_weight,
                    "price_flag": "ok",
                }
            )
    return total, market, lines


def create_snapshot_run(session: Session, as_of: date, moment: datetime) -> SnapshotRun:
    account = the_account(session)
    if not account:
        raise DomainError("请先建立练习账户")
    if as_of < account.created_on:
        raise DomainError("快照日期早于账户建立日")
    days = trading_days_between(session, account.created_on, as_of)
    if not days and account.created_on <= as_of:
        # 建仓日可能不是交易日，快照从之后的交易日开始。
        days = trading_days_between(session, account.created_on, as_of)
    run = SnapshotRun(account_id=account.id, as_of=as_of, created_at=moment, status="succeeded", error="", is_current=False)
    if not days:
        run.status = "empty"
        run.error = "还没有已收盘的练习日"
        session.add(run)
        session.flush()
        return run
    binding = binding_on(session, account.id, days[-1])
    plan = session.get(AllocationPlan, binding.plan_id) if binding else None
    benchmark = session.get(Benchmark, plan.benchmark_id) if plan else None
    benchmark_instrument = benchmark.instrument_id if benchmark else None
    prev_total = ZERO
    prev_day: date | None = None
    nav = ONE
    bench_nav = ONE
    prev_bench: Decimal | None = None
    snapshots: list[tuple[dict, list[dict]]] = []
    for day in days:
        state = project(session, account.id, day)
        total, market, lines = mark_to_market(session, account, day, state)
        if prev_day is None:
            net_flow = sum((flow.amount if flow.direction == "in" else -flow.amount for flow in session.scalars(select(CashFlow).where(CashFlow.account_id == account.id, CashFlow.occurred_on <= day))), ZERO)
        else:
            net_flow = sum(
                (
                    flow.amount if flow.direction == "in" else -flow.amount
                    for flow in session.scalars(
                        select(CashFlow).where(
                            CashFlow.account_id == account.id,
                            CashFlow.occurred_on > prev_day,
                            CashFlow.occurred_on <= day,
                        )
                    )
                ),
                ZERO,
            )
        net_flow = money(net_flow)
        total = money(total)
        pnl = money(total - prev_total - net_flow)
        base = net_flow if prev_day is None else prev_total
        daily_return = (pnl / base) if base else ZERO
        nav = nav * (ONE + daily_return)
        bench_value = None
        if benchmark_instrument:
            current = bar_on(session, benchmark_instrument, day)
            if current and prev_bench is not None:
                bench_nav = bench_nav * (current.close / prev_bench)
                bench_value = bench_nav
            elif current:
                bench_value = ONE
            if current:
                prev_bench = current.close
        snapshots.append(
            (
                {
                    "trade_date": day,
                    "total_value": total,
                    "cash": money(state.cash),
                    "market_value": money(market),
                    "net_flow": net_flow,
                    "daily_pnl": pnl,
                    "daily_return": daily_return,
                    "nav": nav,
                    "cumulative_return": nav - ONE,
                    "benchmark_nav": bench_value,
                },
                lines,
            )
        )
        prev_total = total
        prev_day = day
    for old in session.scalars(select(SnapshotRun).where(SnapshotRun.account_id == account.id, SnapshotRun.is_current.is_(True))):
        old.is_current = False
    run.is_current = True
    session.add(run)
    session.flush()
    for header, lines in snapshots:
        snap = DailySnapshot(run_id=run.id, account_id=account.id, **header)
        session.add(snap)
        session.flush()
        for line in lines:
            session.add(SnapshotLine(snapshot_id=snap.id, **line))
    session.flush()
    return run


def activate_plan(session: Session, plan_id: int, moment: datetime) -> AllocationPlan:
    plan = session.get(AllocationPlan, plan_id)
    if not plan or plan.status != "active":
        raise DomainError("方案不存在或已归档")
    for item in session.scalars(select(AllocationPlan)).all():
        item.is_primary = item.id == plan.id
    account = the_account(session)
    if account:
        current = binding_on(session, account.id, date(9999, 12, 31))
        effective = moment.astimezone(SH).date()
        if not current or current.plan_id != plan.id:
            session.add(PlanBinding(account_id=account.id, plan_id=plan.id, effective_on=effective))
    session.flush()
    return plan


def archive_plan(session: Session, plan_id: int) -> AllocationPlan:
    plan = session.get(AllocationPlan, plan_id)
    if not plan:
        raise DomainError("方案不存在")
    if plan.is_primary:
        raise DomainError("生效中的方案请先切换，再归档")
    plan.status = "archived"
    session.flush()
    return plan


def estimate_view(session: Session, moment: datetime) -> dict:
    account = the_account(session)
    if not account:
        return {"account": None}
    day = moment.astimezone(SH).date()
    state = project(session, account.id, date(9999, 12, 31))
    plan, assets = targets_on(session, account.id, day)
    threshold = plan.rebalance_threshold if plan else d("0.05")
    target_by_instrument = {asset.instrument_id: asset for asset in assets if asset.instrument_id}
    cash_target = sum((asset.target_weight for asset in assets if asset.asset_class == "cash"), ZERO)
    market = ZERO
    rows = []
    warnings = []
    for instrument_id, pos in state.positions.items():
        if pos.qty <= 0:
            continue
        instrument = session.get(Instrument, instrument_id)
        quote = latest_close(session, instrument_id, day)
        if not quote:
            warnings.append(f"{instrument.name} 没有行情，未计入市值")
            continue
        price_day, price = quote
        fx = fx_on(session, instrument.currency, price_day, strict=False)
        if fx is None:
            warnings.append(f"{instrument.name} 缺少 {instrument.currency} 兑人民币汇率，未计入市值")
            continue
        value = money(price * pos.qty * fx)
        market += value
        target = target_by_instrument.get(instrument_id)
        target_weight = target.target_weight if target else ZERO
        rows.append(
            {
                "instrument_id": instrument_id,
                "symbol": instrument.symbol,
                "name": instrument.name,
                "asset_class": instrument.asset_class,
                "quantity": pos.qty,
                "avg_cost": pos.avg_cost,
                "price": price,
                "price_date": price_day,
                "market_value": value,
                "pnl": money((price - pos.avg_cost) * pos.qty),
                "sellable": state.sellable(instrument_id) if price_day >= day or not is_trading_day(session, day) else state.sellable(instrument_id),
                "target_weight": target_weight,
                "dividend_synced": instrument.dividend_synced_at is not None,
            }
        )
        if instrument.dividend_synced_at is None:
            warnings.append(f"{instrument.name} 的分红数据尚未同步")
    for asset in assets:
        if asset.instrument_id and asset.instrument_id not in {row["instrument_id"] for row in rows} and asset.target_weight > 0:
            instrument = session.get(Instrument, asset.instrument_id)
            rows.append(
                {
                    "instrument_id": asset.instrument_id,
                    "symbol": instrument.symbol,
                    "name": instrument.name,
                    "asset_class": instrument.asset_class,
                    "quantity": 0,
                    "avg_cost": ZERO,
                    "price": None,
                    "price_date": None,
                    "market_value": ZERO,
                    "pnl": ZERO,
                    "sellable": 0,
                    "target_weight": asset.target_weight,
                    "dividend_synced": True,
                }
            )
    cash = money(state.cash)
    total = cash + market
    weights = []
    for row in rows:
        actual = (row["market_value"] / total) if total else ZERO
        deviation = actual - row["target_weight"]
        row["actual_weight"] = actual
        row["deviation"] = deviation
        row["breached"] = abs(deviation) > threshold
        weights.append(row)
    cash_actual = (cash / total) if total else ZERO
    cash_deviation = cash_actual - cash_target
    weights.append(
        {
            "instrument_id": None,
            "symbol": "CASH",
            "name": "现金",
            "asset_class": "cash",
            "quantity": cash,
            "market_value": cash,
            "target_weight": cash_target,
            "actual_weight": cash_actual,
            "deviation": cash_deviation,
            "breached": abs(cash_deviation) > threshold,
            "pnl": ZERO,
            "price": None,
        }
    )
    return {
        "cash": cash,
        "market_value": market,
        "total": total,
        "threshold": threshold,
        "weights": weights,
        "warnings": warnings,
        "next_session": submission_session(session, moment),
    }


def current_run(session: Session) -> SnapshotRun | None:
    account = the_account(session)
    if not account:
        return None
    return session.scalar(
        select(SnapshotRun).where(SnapshotRun.account_id == account.id, SnapshotRun.is_current.is_(True)).limit(1)
    )


def fee_rate(session: Session) -> Decimal:
    row = session.get(Setting, "fee_rate")
    if not row:
        return ZERO
    rate = d(row.value)
    if rate < 0:
        return ZERO
    return rate


def fx_on(session: Session, currency: str, day: date, strict: bool = True) -> Decimal | None:
    if not currency or currency == "CNY":
        return ONE
    row = session.scalar(
        select(FxRate).where(FxRate.currency == currency, FxRate.trade_date <= day).order_by(FxRate.trade_date.desc()).limit(1)
    )
    if row:
        return row.rate
    if strict:
        raise DomainError(f"缺少 {currency} 在 {day.isoformat()} 兑人民币的汇率")
    return None


def materialize_reinvest(session: Session, as_of: date, moment: datetime) -> list[Trade]:
    """分红再投资只从开关打开的那天起生成，并且只生成一次。"""
    account = the_account(session)
    plan = active_plan(session)
    if not account or not plan or not account.dividend_reinvest or not account.reinvest_since:
        return []
    actions = session.scalars(
        select(CorporateAction).where(
            CorporateAction.ex_date >= account.reinvest_since,
            CorporateAction.ex_date <= as_of,
            CorporateAction.cash_per_share > 0,
        )
    ).all()
    created: list[Trade] = []
    for action in actions:
        exists = session.scalar(
            select(Trade.id).where(
                Trade.account_id == account.id,
                Trade.instrument_id == action.instrument_id,
                Trade.trade_date == action.ex_date,
                Trade.source == "dividend_reinvest",
                Trade.status == "filled",
            )
        )
        if exists:
            continue
        bar = bar_on(session, action.instrument_id, action.ex_date)
        instrument = session.get(Instrument, action.instrument_id)
        if not bar or not instrument:
            continue
        fx = fx_on(session, instrument.currency, action.ex_date, strict=False)
        if fx is None:
            continue
        if action.record_date:
            held = project(session, account.id, action.record_date).positions.get(action.instrument_id, Pos()).qty
        else:
            held = project(session, account.id, action.ex_date - timedelta(days=1)).positions.get(action.instrument_id, Pos()).qty
        div_cash = money(d(held) * action.cash_per_share * fx)
        rate = fee_rate(session)
        quantity = int((div_cash / (bar.close * fx)) // instrument.lot_size) * instrument.lot_size
        while quantity > 0 and money(bar.close * quantity * fx * (ONE + rate)) > div_cash:
            quantity -= instrument.lot_size
        if quantity <= 0:
            continue
        state = project(session, account.id, action.ex_date)
        gross = money(bar.close * quantity * fx)
        fee = money(gross * rate)
        cost = gross + fee
        if cost > state.cash:
            continue
        trade = Trade(
            account_id=account.id,
            plan_id=plan.id,
            instrument_id=instrument.id,
            side="buy",
            quantity_requested=quantity,
            amount_requested=cost,
            quantity=quantity,
            price=bar.close,
            amount=cost,
            fee=fee,
            status="filled",
            trade_date=action.ex_date,
            submitted_at=moment,
            filled_at=moment,
            is_temporary=False,
            source="dividend_reinvest",
        )
        session.add(trade)
        session.flush()
        created.append(trade)
    return created
