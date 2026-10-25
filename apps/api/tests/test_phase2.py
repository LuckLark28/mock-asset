from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.analytics import brinson, ignore_task, refresh_tasks, repair_bar, risk_metrics, run_backtest
from app.db import Base
from app.domain import (
    SH,
    create_account,
    create_snapshot_run,
    current_run,
    fill_pending,
    materialize_reinvest,
    project,
    save_weight_version,
    submit_order,
)
from app.models import AllocationPlan, Benchmark, ClassBenchmark, CorporateAction, DailySnapshot, Instrument, MarketBar, Setting, SnapshotRun, Trade

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
MON = date(2026, 10, 12)
TUE = date(2026, 10, 13)
WED = date(2026, 10, 14)


def at(day: date, hour: int):
    return __import__("datetime").datetime(day.year, day.month, day.day, hour, 0, tzinfo=SH)


@pytest.fixture()
def session():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    yield db
    db.close()


def add_instrument(session, symbol, asset_class, tradable=True):
    item = Instrument(symbol=symbol, name=symbol, asset_class=asset_class, exchange="SH", currency="CNY", lot_size=100, tradable=tradable)
    session.add(item)
    session.flush()
    return item


def test_metrics_stay_hidden_until_the_sample_is_long_enough():
    short = risk_metrics([Decimal("1"), Decimal("1.2"), Decimal("1.1")], [Decimal("0.2"), Decimal("-0.0833")], Decimal("0"))
    assert short["volatility"] is None
    assert short["sharpe"] is None
    assert short["max_drawdown"] < 0
    assert short["calmar"] is not None
    flat = risk_metrics([Decimal("1"), Decimal("1")], [Decimal("0")], Decimal("0"))
    assert flat["calmar"] is None
    nav = Decimal("1")
    navs = [nav]
    returns = []
    for index in range(24):
        step = Decimal("0.9") if index == 8 else Decimal("1.004")
        nav *= step
        navs.append(nav)
        returns.append(navs[-1] / navs[-2] - 1)
    full = risk_metrics(navs, returns, Decimal("0"))
    assert full["volatility"] is not None
    assert full["sharpe"] is not None
    assert full["max_drawdown"] < 0
    assert full["calmar"] is not None


def test_brinson_residual_closes_the_table(session):
    equity = add_instrument(session, "510300", "equity")
    index = add_instrument(session, "000300", "index", tradable=False)
    moment = at(MON, 9)
    benchmark = Benchmark(name="沪深300", kind="index", instrument_id=index.id)
    session.add(benchmark)
    session.flush()
    plan = AllocationPlan(
        name="全股票",
        description="",
        strategy_tag="",
        benchmark_id=benchmark.id,
        rebalance_threshold=Decimal("0.05"),
        status="active",
        is_primary=True,
        created_at=moment,
    )
    session.add(plan)
    session.flush()
    save_weight_version(session, plan, [{"instrument_id": equity.id, "asset_class": "equity", "target_weight": "1"}], MON, moment)
    session.add(ClassBenchmark(asset_class="equity", instrument_id=index.id))
    session.add(MarketBar(instrument_id=index.id, trade_date=MON, close=Decimal("100"), source="test"))
    session.add(MarketBar(instrument_id=index.id, trade_date=TUE, close=Decimal("110"), source="test"))
    create_account(session, "练习", Decimal("100000"), plan.id, moment)
    create_snapshot_run(session, TUE, at(TUE, 18))
    result = brinson(session, MON, TUE)
    assert result["excess"] == result["allocation"] + result["selection"] + result["residual"]
    assert result["allocation"] == Decimal("-0.100000")
    assert result["residual"] == Decimal("0.000000")


def test_ignored_rebalance_waits_until_the_gap_widens(session):
    equity = add_instrument(session, "510300", "equity")
    moment = at(MON, 9)
    benchmark_instrument = add_instrument(session, "000300", "index", tradable=False)
    benchmark = Benchmark(name="沪深300", kind="index", instrument_id=benchmark_instrument.id)
    session.add(benchmark)
    session.flush()
    plan = AllocationPlan(
        name="一半",
        description="",
        strategy_tag="",
        benchmark_id=benchmark.id,
        rebalance_threshold=Decimal("0.05"),
        status="active",
        is_primary=True,
        created_at=moment,
    )
    session.add(plan)
    session.flush()
    save_weight_version(
        session,
        plan,
        [
            {"instrument_id": equity.id, "asset_class": "equity", "target_weight": "0.5"},
            {"instrument_id": None, "asset_class": "cash", "target_weight": "0.5"},
        ],
        MON,
        moment,
    )
    create_account(session, "练习", Decimal("100000"), plan.id, moment)
    session.add(MarketBar(instrument_id=equity.id, trade_date=MON, close=Decimal("100"), source="test"))
    submit_order(session, equity.id, "buy", at(MON, 10), {"reason": "建仓"}, quantity=500)
    fill_pending(session, MON, at(MON, 18))
    session.add(MarketBar(instrument_id=equity.id, trade_date=WED, close=Decimal("140"), source="test"))
    rows = refresh_tasks(session, at(WED, 10))
    open_rows = [row for row in rows if row.status == "open"]
    assert open_rows
    for row in open_rows:
        ignore_task(session, row.id, "先观察两天")
    again = [row for row in refresh_tasks(session, at(WED, 11)) if row.status == "open"]
    assert again == []
    session.add(MarketBar(instrument_id=equity.id, trade_date=WED + timedelta(days=1), close=Decimal("180"), source="test"))
    widened = [row for row in refresh_tasks(session, at(WED + timedelta(days=1), 10)) if row.status == "open"]
    assert widened


def test_backtest_does_not_touch_the_live_ledger(session):
    equity = add_instrument(session, "510300", "equity")
    for offset, close in enumerate(["10", "10.5", "11"]):
        session.add(MarketBar(instrument_id=equity.id, trade_date=MON + timedelta(days=offset), close=Decimal(close), source="test"))
    before = session.scalar(select(func.count(Trade.id)))
    result = run_backtest(
        session,
        "草稿",
        MON,
        WED,
        [{"instrument_id": equity.id, "target_weight": "1"}],
        "none",
        at(WED, 18),
    )
    assert session.scalar(select(func.count(Trade.id))) == before
    assert session.scalar(select(func.count(DailySnapshot.id))) == 0
    assert result["series"]
    assert "历史表现不代表未来" in result["disclaimer"]


def test_fee_applies_only_to_later_fills(session):
    equity = add_instrument(session, "510300", "equity")
    moment = at(MON, 9)
    benchmark_instrument = add_instrument(session, "000300", "index", tradable=False)
    benchmark = Benchmark(name="沪深300", kind="index", instrument_id=benchmark_instrument.id)
    session.add(benchmark)
    session.flush()
    plan = AllocationPlan(
        name="测试",
        description="",
        strategy_tag="",
        benchmark_id=benchmark.id,
        rebalance_threshold=Decimal("0.05"),
        status="active",
        is_primary=True,
        created_at=moment,
    )
    session.add(plan)
    session.flush()
    save_weight_version(session, plan, [{"instrument_id": equity.id, "asset_class": "equity", "target_weight": "1"}], MON, moment)
    create_account(session, "练习", Decimal("100000"), plan.id, moment)
    session.add(MarketBar(instrument_id=equity.id, trade_date=MON, close=Decimal("4"), source="test"))
    session.add(MarketBar(instrument_id=equity.id, trade_date=TUE, close=Decimal("4"), source="test"))
    first = submit_order(session, equity.id, "buy", at(MON, 10), {"reason": "先买"}, quantity=1000)
    fill_pending(session, MON, at(MON, 18))
    assert first.amount == Decimal("4000.00")
    session.add(Setting(key="fee_rate", value="0.001"))
    second = submit_order(session, equity.id, "buy", at(TUE, 10), {"reason": "再买"}, quantity=1000)
    fill_pending(session, TUE, at(TUE, 18))
    assert first.amount == Decimal("4000.00")
    assert second.fee == Decimal("4.00")
    assert second.amount == Decimal("4004.00")


def test_reinvestment_buys_shares_and_cash_dividend_does_not(session):
    equity = add_instrument(session, "510300", "equity")
    moment = at(MON, 9)
    benchmark_instrument = add_instrument(session, "000300", "index", tradable=False)
    benchmark = Benchmark(name="沪深300", kind="index", instrument_id=benchmark_instrument.id)
    session.add(benchmark)
    session.flush()
    plan = AllocationPlan(
        name="测试",
        description="",
        strategy_tag="",
        benchmark_id=benchmark.id,
        rebalance_threshold=Decimal("0.05"),
        status="active",
        is_primary=True,
        created_at=moment,
    )
    session.add(plan)
    session.flush()
    save_weight_version(session, plan, [{"instrument_id": equity.id, "asset_class": "equity", "target_weight": "1"}], MON, moment)
    account = create_account(session, "练习", Decimal("100000"), plan.id, moment)
    session.add(MarketBar(instrument_id=equity.id, trade_date=MON, close=Decimal("4"), source="test"))
    session.add(MarketBar(instrument_id=equity.id, trade_date=WED, close=Decimal("4"), source="test"))
    submit_order(session, equity.id, "buy", at(MON, 10), {"reason": "建仓"}, quantity=10000)
    fill_pending(session, MON, at(MON, 18))
    session.add(CorporateAction(instrument_id=equity.id, ex_date=WED, cash_per_share=Decimal("1"), split_ratio=Decimal("1"), source="test"))
    cash_only = project(session, account.id, WED)
    assert cash_only.positions[equity.id].qty == 10000
    assert cash_only.cash == Decimal("70000.00")
    account.dividend_reinvest = True
    account.reinvest_since = WED
    created = materialize_reinvest(session, WED, at(WED, 18))
    assert len(created) == 1
    again = materialize_reinvest(session, WED, at(WED, 18))
    assert again == []
    invested = project(session, account.id, WED)
    assert invested.positions[equity.id].qty == 12500
    assert invested.cash == Decimal("60000.00")


def test_split_doubles_shares_and_halves_cost(session):
    equity = add_instrument(session, "510300", "equity")
    moment = at(MON, 9)
    benchmark_instrument = add_instrument(session, "000300", "index", tradable=False)
    benchmark = Benchmark(name="沪深300", kind="index", instrument_id=benchmark_instrument.id)
    session.add(benchmark)
    session.flush()
    plan = AllocationPlan(
        name="测试",
        description="",
        strategy_tag="",
        benchmark_id=benchmark.id,
        rebalance_threshold=Decimal("0.05"),
        status="active",
        is_primary=True,
        created_at=moment,
    )
    session.add(plan)
    session.flush()
    save_weight_version(session, plan, [{"instrument_id": equity.id, "asset_class": "equity", "target_weight": "1"}], MON, moment)
    account = create_account(session, "练习", Decimal("100000"), plan.id, moment)
    session.add(MarketBar(instrument_id=equity.id, trade_date=MON, close=Decimal("4"), source="test"))
    submit_order(session, equity.id, "buy", at(MON, 10), {"reason": "建仓"}, quantity=10000)
    fill_pending(session, MON, at(MON, 18))
    session.add(CorporateAction(instrument_id=equity.id, ex_date=WED, cash_per_share=Decimal("0"), split_ratio=Decimal("2"), source="test"))
    state = project(session, account.id, WED)
    assert state.positions[equity.id].qty == 20000
    assert state.positions[equity.id].avg_cost == Decimal("2.0000")
    assert state.cash == Decimal("60000.00")


def test_repaired_price_creates_a_new_snapshot_version(session):
    equity = add_instrument(session, "510300", "equity")
    moment = at(MON, 9)
    benchmark_instrument = add_instrument(session, "000300", "index", tradable=False)
    benchmark = Benchmark(name="沪深300", kind="index", instrument_id=benchmark_instrument.id)
    session.add(benchmark)
    session.flush()
    plan = AllocationPlan(
        name="测试",
        description="",
        strategy_tag="",
        benchmark_id=benchmark.id,
        rebalance_threshold=Decimal("0.05"),
        status="active",
        is_primary=True,
        created_at=moment,
    )
    session.add(plan)
    session.flush()
    save_weight_version(session, plan, [{"instrument_id": equity.id, "asset_class": "equity", "target_weight": "1"}], MON, moment)
    create_account(session, "练习", Decimal("100000"), plan.id, moment)
    session.add(MarketBar(instrument_id=equity.id, trade_date=MON, close=Decimal("4"), source="test"))
    submit_order(session, equity.id, "buy", at(MON, 10), {"reason": "建仓"}, quantity=1000)
    fill_pending(session, MON, at(MON, 18))
    first = create_snapshot_run(session, MON, at(MON, 18))
    with pytest.raises(Exception):
        repair_bar(session, equity.id, MON, Decimal("5"))
    repair_bar(session, equity.id, TUE, Decimal("4.2"))
    second = create_snapshot_run(session, TUE, at(TUE, 18))
    assert first.id != second.id
    assert current_run(session).id == second.id
    assert session.get(SnapshotRun, first.id).is_current is False


@pytest.mark.parametrize('kind', ['dividend', 'split', 'combined', 'missing_bar'])
def test_backtest_corporate_actions_preserve_total_value(session, kind):
    equity = add_instrument(session, '510300', 'equity')
    split = Decimal('2') if kind in {'split', 'combined'} else Decimal('1')
    dividend = Decimal('1') if kind in {'dividend', 'combined', 'missing_bar'} else Decimal('0')
    closes = {MON: Decimal('10'), TUE: (Decimal('10') - dividend) / split, WED: (Decimal('10') - dividend) / split}
    for day, close in closes.items():
        if kind != 'missing_bar' or day != TUE:
            session.add(MarketBar(instrument_id=equity.id, trade_date=day, close=close, source='test'))
    session.add(CorporateAction(instrument_id=equity.id, ex_date=TUE, record_date=MON, cash_per_share=dividend, split_ratio=split, source='test'))
    session.flush()
    result = run_backtest(session, '公司行动', MON, WED, [{'instrument_id': equity.id, 'target_weight': '1'}], 'none', at(WED, 18))
    assert len(result['series']) == 3
    assert result['series'][-1]['nav'] == Decimal('1')
    assert result['metrics']['cumulative_return'] == Decimal('0')
    assert session.scalar(select(func.count(Trade.id))) == 0
    assert session.scalar(select(func.count(DailySnapshot.id))) == 0


@pytest.mark.parametrize('fee', ['0', '0.001'])
def test_monthly_backtest_is_independent_of_weight_order(session, fee):
    equity = add_instrument(session, '510300', 'equity')
    bond = add_instrument(session, '511010', 'bond')
    session.add(Setting(key='fee_rate', value=fee))
    start = date(2026, 1, 30)
    end = date(2026, 2, 27)
    day = start
    while day <= end:
        if day.weekday() < 5:
            prices = ('10', '10') if day == start else ('9', '11') if day == date(2026, 2, 2) else ('10', '11')
            for instrument, close in zip((equity, bond), prices):
                session.add(MarketBar(instrument_id=instrument.id, trade_date=day, close=Decimal(close), source='test'))
        day += timedelta(days=1)
    session.flush()
    weights = [{'instrument_id': item.id, 'target_weight': '.5'} for item in (equity, bond)]
    left = run_backtest(session, '顺序一', start, end, weights, 'monthly', at(end, 18))
    right = run_backtest(session, '顺序二', start, end, list(reversed(weights)), 'monthly', at(end, 18))
    assert left['series'] == right['series']
    assert left['metrics'] == right['metrics']
    if fee == '0':
        assert left['series'][-1]['nav'] == Decimal('1.0555')
