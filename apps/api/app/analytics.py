"""风险指标、Brinson、再平衡提醒和隔离回测。回测不写主账户流水。"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.domain import (
    CLASSES,
    ONE,
    SH,
    ZERO,
    DomainError,
    Pos,
    State,
    active_plan,
    apply_corporate_action,
    bar_on,
    d,
    estimate_view,
    fee_rate,
    fx_on,
    is_trading_day,
    latest_close,
    money,
    submit_order,
    the_account,
    trading_days_between,
)
from app.models import (
    BacktestRun,
    ClassBenchmark,
    CorporateAction,
    DailySnapshot,
    FxRate,
    Instrument,
    MarketBar,
    RebalanceTask,
    Setting,
    SnapshotLine,
    Trade,
)

Q = Decimal("0.000001")


def _q(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(Q, rounding=ROUND_HALF_UP)


def setting_value(session: Session, key: str, default: str) -> str:
    row = session.get(Setting, key)
    return row.value if row else default


def risk_free_rate(session: Session) -> Decimal:
    return d(setting_value(session, "risk_free_rate", "0"))


def risk_metrics(navs: list[Decimal], returns: list[Decimal], rf: Decimal) -> dict:
    notes: list[str] = []
    periods = max(len(navs) - 1, 0)
    annualized = None
    if periods and navs[-1] > 0:
        annualized = navs[-1] ** (Decimal(252) / Decimal(periods)) - ONE
    volatility = None
    if len(returns) >= 20:
        mean = sum(returns, ZERO) / Decimal(len(returns))
        variance = sum((item - mean) ** 2 for item in returns) / Decimal(len(returns) - 1)
        volatility = variance.sqrt() * Decimal(252).sqrt()
    else:
        notes.append("日收益不足 20 个，不显示波动率和夏普")
    drawdown = ZERO
    peak = navs[0] if navs else ONE
    for nav in navs:
        peak = max(peak, nav)
        if peak:
            drawdown = min(drawdown, nav / peak - ONE)
    sharpe = (annualized - rf) / volatility if annualized is not None and volatility else None
    calmar = annualized / abs(drawdown) if annualized is not None and drawdown < 0 else None
    if navs and drawdown == 0:
        notes.append("区间没有回撤，不显示 Calmar")
    return {
        "annualized_return": _q(annualized),
        "volatility": _q(volatility),
        "max_drawdown": _q(drawdown) if navs else None,
        "sharpe": _q(sharpe),
        "calmar": _q(calmar),
        "risk_free_rate": rf,
        "sample_days": len(returns),
        "notes": notes,
    }


def read_settings(session: Session) -> dict:
    account = the_account(session)
    classes = []
    for asset_class in ("equity", "bond", "commodity"):
        row = session.get(ClassBenchmark, asset_class)
        instrument = session.get(Instrument, row.instrument_id) if row else None
        classes.append(
            {
                "asset_class": asset_class,
                "instrument_id": instrument.id if instrument else None,
                "symbol": instrument.symbol if instrument else "",
                "name": instrument.name if instrument else "",
            }
        )
    return {
        "fee_rate": setting_value(session, "fee_rate", "0"),
        "risk_free_rate": setting_value(session, "risk_free_rate", "0"),
        "dividend_reinvest": bool(account and account.dividend_reinvest),
        "reinvest_since": account.reinvest_since if account else None,
        "class_benchmarks": classes,
    }


def save_settings(session: Session, fee: Decimal, rf: Decimal, reinvest: bool, moment: datetime) -> dict:
    if fee < 0 or fee > Decimal("0.03"):
        raise DomainError("手续费率需要在 0 到 3% 之间")
    if rf < Decimal("-0.1") or rf > Decimal("0.2"):
        raise DomainError("无风险利率需要在 -10% 到 20% 之间")
    for key, value in (("fee_rate", fee), ("risk_free_rate", rf)):
        row = session.get(Setting, key)
        if row:
            row.value = format(value, "f")
        else:
            session.add(Setting(key=key, value=format(value, "f")))
    account = the_account(session)
    if account:
        if reinvest and not account.dividend_reinvest:
            account.reinvest_since = moment.astimezone(SH).date()
        account.dividend_reinvest = reinvest
    session.flush()
    return read_settings(session)


def set_class_benchmark(session: Session, asset_class: str, instrument_id: int) -> None:
    if asset_class not in {"equity", "bond", "commodity"}:
        raise DomainError("只有股票、债券、商品需要类别基准")
    instrument = session.get(Instrument, instrument_id)
    if not instrument:
        raise DomainError("基准标的不存在")
    row = session.get(ClassBenchmark, asset_class)
    if row:
        row.instrument_id = instrument.id
    else:
        session.add(ClassBenchmark(asset_class=asset_class, instrument_id=instrument.id))
    session.flush()


def repair_bar(session: Session, instrument_id: int, day: date, close: Decimal) -> MarketBar:
    instrument = session.get(Instrument, instrument_id)
    if not instrument:
        raise DomainError("标的不存在")
    if close <= 0:
        raise DomainError("收盘价需要大于 0")
    if not is_trading_day(session, day):
        raise DomainError("只能补录交易日")
    existing = bar_on(session, instrument_id, day)
    if existing and existing.source != "manual":
        raise DomainError("这一天已经有行情。补录只填缺失的交易日")
    price = close.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if existing:
        existing.close = price
        existing.source = "manual"
        session.flush()
        return existing
    bar = MarketBar(instrument_id=instrument_id, trade_date=day, close=price, source="manual")
    session.add(bar)
    session.flush()
    return bar


def save_fx(session: Session, currency: str, day: date, rate: Decimal) -> FxRate:
    currency = currency.strip().upper()
    if currency in {"", "CNY"} or rate <= 0:
        raise DomainError("请填写外币代码，以及大于 0 的兑人民币汇率")
    row = session.scalar(select(FxRate).where(FxRate.currency == currency, FxRate.trade_date == day))
    if row:
        row.rate = rate
        row.source = "manual"
    else:
        row = FxRate(currency=currency, trade_date=day, rate=rate, source="manual")
        session.add(row)
    session.flush()
    return row


def brinson(session: Session, start: date, end: date) -> dict:
    from app.domain import current_run

    run = current_run(session)
    if not run or run.status != "succeeded":
        raise DomainError("还没有成功的快照，无法归因")
    snaps = session.scalars(
        select(DailySnapshot)
        .where(DailySnapshot.run_id == run.id, DailySnapshot.trade_date >= start, DailySnapshot.trade_date <= end)
        .order_by(DailySnapshot.trade_date)
    ).all()
    if len(snaps) < 2:
        raise DomainError("这个区间至少需要两个快照")
    first, last = snaps[0], snaps[-1]
    first_lines = session.scalars(select(SnapshotLine).where(SnapshotLine.snapshot_id == first.id)).all()
    last_lines = session.scalars(select(SnapshotLine).where(SnapshotLine.snapshot_id == last.id)).all()
    trades = session.scalars(
        select(Trade)
        .where(Trade.status == "filled", Trade.trade_date > first.trade_date, Trade.trade_date <= last.trade_date)
        .options(selectinload(Trade.instrument))
    ).all()
    rows = []
    allocation_sum = ZERO
    selection_sum = ZERO
    for asset_class in ("equity", "bond", "commodity", "cash"):
        start_weight = sum((line.actual_weight for line in first_lines if line.asset_class == asset_class), ZERO)
        target_weight = sum((line.target_weight for line in first_lines if line.asset_class == asset_class), ZERO)
        start_value = sum((line.market_value for line in first_lines if line.asset_class == asset_class), ZERO)
        end_value = sum((line.market_value for line in last_lines if line.asset_class == asset_class), ZERO)
        if asset_class == "cash":
            actual_return = ZERO
            benchmark_return = ZERO
            benchmark_name = "现金收益按 0"
        else:
            flow = ZERO
            for trade in trades:
                if trade.instrument.asset_class != asset_class:
                    continue
                signed = trade.amount if trade.side == "buy" else -trade.amount
                flow += signed
            actual_return = (end_value - start_value - flow) / start_value if start_value else ZERO
            mapping = session.get(ClassBenchmark, asset_class)
            benchmark_return = ZERO
            benchmark_name = "未设置"
            if mapping:
                instrument = session.get(Instrument, mapping.instrument_id)
                benchmark_name = instrument.name if instrument else "未设置"
                begin = latest_close(session, mapping.instrument_id, first.trade_date)
                finish = latest_close(session, mapping.instrument_id, last.trade_date)
                if begin and finish and begin[1]:
                    benchmark_return = finish[1] / begin[1] - ONE
        allocation = (start_weight - target_weight) * benchmark_return
        selection = start_weight * (actual_return - benchmark_return)
        allocation_sum += allocation
        selection_sum += selection
        rows.append(
            {
                "asset_class": asset_class,
                "actual_weight": _q(start_weight),
                "target_weight": _q(target_weight),
                "actual_return": _q(actual_return),
                "benchmark_return": _q(benchmark_return),
                "benchmark_name": benchmark_name,
                "allocation": _q(allocation),
                "selection": _q(selection),
            }
        )
    portfolio = last.nav / first.nav - ONE if first.nav else ZERO
    if first.benchmark_nav and last.benchmark_nav:
        benchmark = last.benchmark_nav / first.benchmark_nav - ONE
        excess = portfolio - benchmark
        residual = excess - allocation_sum - selection_sum
    else:
        benchmark = None
        excess = None
        residual = None
    return {
        "start": first.trade_date,
        "end": last.trade_date,
        "portfolio_return": _q(portfolio),
        "benchmark_return": _q(benchmark),
        "excess": _q(excess),
        "allocation": _q(allocation_sum),
        "selection": _q(selection_sum),
        "residual": _q(residual),
        "rows": rows,
        "note": "配置效应用实际类别权重减去目标权重，再乘类别基准收益。选择效应用实际权重乘类别超额。残差是超额减这两项，用来让表加总。",
    }


def refresh_tasks(session: Session, moment: datetime) -> list[RebalanceTask]:
    account = the_account(session)
    plan = active_plan(session)
    view = estimate_view(session, moment)
    if not account or not plan or "weights" not in view:
        return []
    threshold = view["threshold"]
    today = moment.astimezone(SH).date()
    open_rows = session.scalars(
        select(RebalanceTask).where(RebalanceTask.account_id == account.id, RebalanceTask.status == "open")
    ).all()
    by_key = {(row.instrument_id, row.asset_class): row for row in open_rows}
    floor: dict[tuple, Decimal] = {}
    ignored = session.scalars(
        select(RebalanceTask)
        .where(RebalanceTask.account_id == account.id, RebalanceTask.status == "ignored")
        .order_by(RebalanceTask.id)
    ).all()
    for row in ignored:
        floor[(row.instrument_id, row.asset_class)] = row.extra_threshold
    for weight in view["weights"]:
        key = (weight["instrument_id"], weight["asset_class"])
        deviation = weight["deviation"]
        band = floor.get(key, threshold)
        existing = by_key.get(key)
        if abs(deviation) <= threshold:
            if existing:
                existing.status = "cleared"
            continue
        if abs(deviation) < band:
            continue
        suggested = money(-deviation * view["total"])
        if existing:
            existing.deviation = deviation
            existing.suggested_amount = suggested
            continue
        session.add(
            RebalanceTask(
                account_id=account.id,
                plan_id=plan.id,
                instrument_id=weight["instrument_id"],
                asset_class=weight["asset_class"],
                name=weight["name"],
                opened_on=today,
                deviation=deviation,
                suggested_amount=suggested,
                status="open",
                extra_threshold=floor.get(key, ZERO),
            )
        )
    session.flush()
    return session.scalars(select(RebalanceTask).where(RebalanceTask.account_id == account.id).order_by(RebalanceTask.id.desc())).all()


def task_stats(rows: list[RebalanceTask]) -> dict:
    return {
        "open": sum(1 for row in rows if row.status == "open"),
        "executed": sum(1 for row in rows if row.status == "executed"),
        "ignored": sum(1 for row in rows if row.status == "ignored"),
    }


def ignore_task(session: Session, task_id: int, reason: str) -> RebalanceTask:
    task = session.get(RebalanceTask, task_id)
    if not task or task.status != "open":
        raise DomainError("这条提醒已经处理过")
    if not reason.strip():
        raise DomainError("请填写忽略理由")
    task.status = "ignored"
    task.ignore_reason = reason.strip()
    task.extra_threshold = abs(task.deviation) + Decimal("0.02")
    session.flush()
    return task


def execute_task(session: Session, task_id: int, moment: datetime, log_payload: dict, checklist_json: str = "") -> RebalanceTask:
    task = session.get(RebalanceTask, task_id)
    if not task or task.status != "open":
        raise DomainError("这条提醒已经处理过")
    if task.instrument_id is None:
        raise DomainError("现金偏离请用出入金处理，不会自动下单")
    side = "sell" if task.deviation > 0 else "buy"
    trade = submit_order(
        session,
        task.instrument_id,
        side,
        moment,
        log_payload,
        amount=abs(task.suggested_amount),
        source="rebalance",
        checklist_json=checklist_json,
    )
    task.status = "executed"
    task.trade_id = trade.id
    session.flush()
    return task


def trade_outcome(session: Session, trade: Trade, day: date) -> dict | None:
    if trade.status != "filled" or not trade.price:
        return None
    quote = latest_close(session, trade.instrument_id, day)
    if not quote:
        return None
    _, price = quote
    return {"latest_price": price, "price_date": quote[0], "since_return": _q(price / trade.price - ONE)}


def run_backtest(
    session: Session,
    name: str,
    start: date,
    end: date,
    weights: list[dict],
    frequency: str,
    moment: datetime,
) -> dict:
    if frequency not in {"none", "monthly"}:
        raise DomainError("再平衡只能选不调整或每月")
    if end < start:
        raise DomainError("结束日需要不早于开始日")
    normalized: list[tuple[Instrument, Decimal]] = []
    total_weight = ZERO
    for item in weights:
        weight = d(item["target_weight"])
        if weight < 0:
            raise DomainError("权重不能为负")
        if weight == 0:
            continue
        instrument = session.get(Instrument, item["instrument_id"])
        if not instrument or not instrument.tradable:
            raise DomainError("回测标的需要可交易")
        total_weight += weight
        normalized.append((instrument, weight))
    if not normalized:
        raise DomainError("请至少选择一个标的")
    if abs(total_weight - ONE) > Decimal("0.0001"):
        raise DomainError(f"草稿权重合计需要为 100%，当前为 {total_weight * 100:.2f}%")
    before_trades = session.scalar(select(func.count(Trade.id))) or 0
    before_snaps = session.scalar(select(func.count(DailySnapshot.id))) or 0
    days = [day for day in trading_days_between(session, start, end) if all(bar_on(session, instrument.id, day) for instrument, _ in normalized)]
    if len(days) < 2:
        raise DomainError("这个区间里同时有行情的交易日不足两天。请先同步行情，或缩短到已有收盘价的区间")
    # 固定顺序解决资金紧张时的分配歧义；同一输入集合不受排列影响。
    if len({instrument.id for instrument, _ in normalized}) != len(normalized):
        raise DomainError("同一标的不能重复")
    normalized.sort(key=lambda item: item[0].id)
    days = trading_days_between(session, days[0], days[-1])
    instruments = {instrument.id: instrument for instrument, _ in normalized}
    actions = session.scalars(
        select(CorporateAction).where(
            CorporateAction.instrument_id.in_(instruments),
            CorporateAction.ex_date >= days[0],
            CorporateAction.ex_date <= days[-1],
        ).order_by(CorporateAction.ex_date, CorporateAction.id)
    ).all()
    actions_by_day: dict[date, list[CorporateAction]] = {}
    for action in actions:
        actions_by_day.setdefault(action.ex_date, []).append(action)
    # 非交易日公司行动也进入事件流，避免在缺失行情日漏掉派息。
    timeline = sorted(set(days) | set(actions_by_day))
    rate = fee_rate(session)
    state = State(cash=Decimal("1000000"), positions={key: Pos() for key in instruments})
    history: dict[date, dict[int, int]] = {}
    series = []
    month = None

    def price_on(instrument: Instrument, day: date) -> Decimal:
        quote = latest_close(session, instrument.id, day)
        if not quote or quote[1] <= 0:
            raise DomainError(f"{instrument.name} 没有可用价格")
        return quote[1] * fx_on(session, instrument.currency, day)

    def rebalance(day: date) -> None:
        prices = {instrument.id: price_on(instrument, day) for instrument, _ in normalized}
        total = state.cash + sum(prices[key] * pos.qty for key, pos in state.positions.items())
        deltas = []
        for instrument, weight in normalized:
            price = prices[instrument.id]
            target_qty = int((total * weight / price) // instrument.lot_size) * instrument.lot_size
            deltas.append((instrument, target_qty - state.positions[instrument.id].qty))
        for instrument, delta in deltas:
            if delta < 0:
                gross = money(prices[instrument.id] * -delta)
                state.cash += gross - money(gross * rate)
                state.positions[instrument.id].qty += delta
        for instrument, delta in deltas:
            if delta <= 0:
                continue
            price = prices[instrument.id]
            affordable = int((state.cash / (price * (ONE + rate))) // instrument.lot_size) * instrument.lot_size
            quantity = min(delta, affordable)
            # 按实际分项舍入后的费用检查现金，避免分币误差造成透支。
            while quantity > 0:
                gross = money(price * quantity)
                cost = gross + money(gross * rate)
                if cost <= state.cash:
                    state.cash -= cost
                    state.positions[instrument.id].qty += quantity
                    break
                quantity -= instrument.lot_size

    for day in timeline:
        for action in actions_by_day.get(day, []):
            if action.record_date is None:
                held = state.positions[action.instrument_id].qty
            else:
                record = max((key for key in history if key <= action.record_date), default=None)
                held = history[record].get(action.instrument_id, 0) if record else 0
            instrument = instruments[action.instrument_id]
            apply_corporate_action(state, action, held, fx_on(session, instrument.currency, day))
        if day in days:
            period = (day.year, day.month)
            if (not series or (frequency == "monthly" and period != month)) and all(
                bar_on(session, instrument.id, day) for instrument, _ in normalized
            ):
                rebalance(day)
                month = period
            prices = {instrument.id: price_on(instrument, day) for instrument, _ in normalized}
            total = state.cash + sum(prices[key] * pos.qty for key, pos in state.positions.items())
            series.append({"date": day, "nav": total / Decimal("1000000")})
        history[day] = {key: pos.qty for key, pos in state.positions.items()}
    navs = [item["nav"] for item in series]
    returns = [navs[index] / navs[index - 1] - ONE for index in range(1, len(navs))]
    metrics = risk_metrics(navs, returns, risk_free_rate(session))
    metrics["cumulative_return"] = _q(navs[-1] - ONE)
    payload = {
        "name": name.strip() or "草稿回测",
        "start": days[0],
        "end": days[-1],
        "frequency": frequency,
        "metrics": metrics,
        "series": [{"date": item["date"], "nav": _q(item["nav"])} for item in series],
        "disclaimer": "历史表现不代表未来。使用未复权收盘价，计入现金分红和拆分；分红保留现金，不自动再投资。缺失价格沿用前值，行情齐全时才再平衡。这笔回测没有写入主账户的成交和快照。",
    }
    session.add(
        BacktestRun(
            name=payload["name"],
            start_date=days[0],
            end_date=days[-1],
            rebalance_frequency=frequency,
            metrics_json=json.dumps(
                {"cumulative_return": format(metrics["cumulative_return"], "f") if metrics["cumulative_return"] is not None else None},
                ensure_ascii=False,
            ),
            created_at=moment,
        )
    )
    session.flush()
    after_trades = session.scalar(select(func.count(Trade.id))) or 0
    after_snaps = session.scalar(select(func.count(DailySnapshot.id))) or 0
    if after_trades != before_trades or after_snaps != before_snaps:
        raise DomainError("回测不应改动主账户")
    return payload
