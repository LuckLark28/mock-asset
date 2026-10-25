from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.market import cash_events
from app.domain import (
    DomainError,
    SH,
    activate_plan,
    add_cashflow,
    create_account,
    create_snapshot_run,
    current_run,
    estimate_view,
    fill_pending,
    project,
    save_weight_version,
    submit_order,
)
from app.models import AllocationPlan, Benchmark, CorporateAction, Instrument, MarketBar

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
MON = date(2026, 10, 12)
TUE = date(2026, 10, 13)
WED = date(2026, 10, 14)


def at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=SH)


@pytest.fixture()
def session():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    yield db
    db.close()


def book(session, weights=(("510300", "1"),)):
    moment = at(MON, 9)
    instruments = {}
    for symbol, asset_class in (("510300", "equity"), ("511010", "bond"), ("000300", "index")):
        item = Instrument(symbol=symbol, name=symbol, asset_class=asset_class, exchange="SH", lot_size=100, tradable=asset_class != "index")
        session.add(item)
        instruments[symbol] = item
    session.flush()
    benchmark = Benchmark(name="沪深300", kind="index", instrument_id=instruments["000300"].id)
    session.add(benchmark)
    session.flush()
    plan = AllocationPlan(
        name="测试方案",
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
        [{"instrument_id": instruments[symbol].id, "asset_class": "equity", "target_weight": weight} for symbol, weight in weights],
        MON,
        moment,
    )
    create_account(session, "练习", Decimal("100000"), plan.id, moment)
    return instruments


def bar(session, instrument_id, day, close):
    session.add(MarketBar(instrument_id=instrument_id, trade_date=day, close=Decimal(str(close)), source="test"))


def test_cumulative_dividend_becomes_each_payment():
    events = cash_events(
        [(date(2014, 1, 21), Decimal("0.081")), (date(2012, 12, 18), Decimal("0.033"))],
        cumulative=True,
    )
    assert events == [(date(2012, 12, 18), Decimal("0.033")), (date(2014, 1, 21), Decimal("0.048"))]


def test_weight_must_sum_to_one(session):
    book(session)
    plan = session.query(AllocationPlan).one()
    with pytest.raises(DomainError, match="100%"):
        save_weight_version(
            session,
            plan,
            [{"instrument_id": session.query(Instrument).filter_by(symbol="510300").one().id, "asset_class": "equity", "target_weight": "0.5"}],
            TUE,
            at(TUE, 9),
        )


def test_buy_sell_cost_cash_and_lots(session):
    instruments = book(session)
    equity = instruments["510300"].id
    bar(session, equity, MON, "4")
    account_id = session.query(AllocationPlan).one()
    trade = submit_order(session, equity, "buy", at(MON, 10), {"reason": "建仓"}, quantity=10000)
    assert trade.status == "pending"
    assert trade.trade_date == MON
    filled = fill_pending(session, MON, at(MON, 18))
    assert filled[0].status == "filled"
    assert filled[0].amount == Decimal("40000.00")
    state = project(session, filled[0].account_id, MON)
    assert state.cash == Decimal("60000.00")
    assert state.positions[equity].avg_cost == Decimal("4.0000")

    with pytest.raises(DomainError, match="整数倍"):
        submit_order(session, equity, "buy", at(MON, 11), {"reason": "零股"}, quantity=50)

    same_day_sell = submit_order(session, equity, "sell", at(MON, 14), {"reason": "当天卖"}, quantity=100)
    assert same_day_sell.status == "rejected"
    assert "下一个交易日" in same_day_sell.reject_reason

    bar(session, equity, TUE, "4")
    later = submit_order(session, equity, "sell", at(TUE, 10), {"reason": "减仓"}, quantity=2000)
    fill_pending(session, TUE, at(TUE, 18))
    state = project(session, later.account_id, TUE)
    assert state.positions[equity].qty == 8000
    assert state.cash == Decimal("68000.00")
    assert account_id


def test_reject_when_cash_is_short(session):
    instruments = book(session)
    equity = instruments["510300"].id
    bar(session, equity, MON, "4")
    trade = submit_order(session, equity, "buy", at(MON, 10), {"reason": "超额"}, quantity=30000)
    assert trade.status == "rejected"


def test_dividend_offsets_ex_date_price_drop(session):
    instruments = book(session)
    equity = instruments["510300"].id
    for day, close in ((MON, "4"), (TUE, "4.4"), (WED, "4.3")):
        bar(session, equity, day, close)
        bar(session, instruments["000300"].id, day, "100" if day == MON else "110")
    buy = submit_order(session, equity, "buy", at(MON, 10), {"reason": "建仓"}, quantity=10000)
    fill_pending(session, MON, at(MON, 18))
    session.add(
        CorporateAction(instrument_id=equity, ex_date=WED, record_date=MON, cash_per_share=Decimal("0.1"), source="test")
    )
    run = create_snapshot_run(session, WED, at(WED, 19))
    from app.models import DailySnapshot

    rows = session.query(DailySnapshot).filter_by(run_id=run.id).order_by(DailySnapshot.trade_date).all()
    assert [row.trade_date for row in rows] == [MON, TUE, WED]
    assert rows[0].nav == Decimal("1")
    assert rows[1].daily_pnl == Decimal("4000.00")
    assert rows[1].nav == Decimal("1.04")
    assert rows[2].daily_pnl == Decimal("0.00")
    assert rows[2].cash == Decimal("61000.00")
    assert rows[0].benchmark_nav == Decimal("1")
    assert rows[1].benchmark_nav == Decimal("1.1")
    state = project(session, buy.account_id, WED)
    assert state.cash == Decimal("61000.00")


def test_deposit_does_not_create_return(session):
    instruments = book(session)
    equity = instruments["510300"].id
    bar(session, equity, MON, "4")
    bar(session, equity, TUE, "4")
    submit_order(session, equity, "buy", at(MON, 10), {"skip": True, "confirm_skip": True}, quantity=1000)
    fill_pending(session, MON, at(MON, 18))
    add_cashflow(session, "in", Decimal("10000"), at(TUE, 9), "追加")
    run = create_snapshot_run(session, TUE, at(TUE, 19))
    from app.models import DailySnapshot

    tue = session.query(DailySnapshot).filter_by(run_id=run.id, trade_date=TUE).one()
    assert tue.net_flow == Decimal("10000.00")
    assert tue.daily_pnl == Decimal("0.00")
    assert tue.nav == Decimal("1")


def test_rerun_keeps_previous_version(session):
    instruments = book(session)
    bar(session, instruments["510300"].id, MON, "4")
    first = create_snapshot_run(session, MON, at(MON, 19))
    second = create_snapshot_run(session, MON, at(MON, 20))
    assert first.id != second.id
    assert current_run(session).id == second.id
    assert first.is_current is False


def test_only_one_account(session):
    instruments = book(session)
    plan = session.query(AllocationPlan).one()
    with pytest.raises(DomainError, match="只有一个"):
        create_account(session, "另一个", Decimal("1"), plan.id, at(TUE, 9))
    assert instruments


def test_cash_weight_is_part_of_allocation(session):
    book(session)
    view = estimate_view(session, at(MON, 10))
    cash = next(row for row in view["weights"] if row["asset_class"] == "cash")
    equity = next(row for row in view["weights"] if row["symbol"] == "510300")
    assert cash["actual_weight"] == Decimal("1")
    assert cash["target_weight"] == Decimal("0")
    assert cash["breached"] is True
    assert equity["actual_weight"] == Decimal("0")
    assert equity["target_weight"] == Decimal("1")
    assert equity["breached"] is True


def test_activate_switches_from_today(session):
    book(session)
    moment = at(TUE, 9)
    plan = AllocationPlan(
        name="第二套",
        description="",
        strategy_tag="",
        benchmark_id=session.query(Benchmark).one().id,
        rebalance_threshold=Decimal("0.05"),
        status="active",
        is_primary=False,
        created_at=moment,
    )
    session.add(plan)
    session.flush()
    save_weight_version(
        session,
        plan,
        [{"instrument_id": session.query(Instrument).filter_by(symbol="511010").one().id, "asset_class": "bond", "target_weight": "1"}],
        TUE,
        moment,
    )
    activate_plan(session, plan.id, moment)
    view = estimate_view(session, moment)
    assert any(row["symbol"] == "511010" and row["target_weight"] == Decimal("1") for row in view["weights"])


def test_withdrawal_after_sale_survives_reload_and_snapshot(session):
    instruments = book(session)
    equity = instruments['510300'].id
    for day in (MON, TUE):
        bar(session, equity, day, '4')
    submit_order(session, equity, 'buy', at(MON, 10), {'reason': '建仓'}, quantity=10000)
    fill_pending(session, MON, at(MON, 18))
    submit_order(session, equity, 'sell', at(TUE, 10), {'reason': '卖出'}, quantity=10000)
    fill_pending(session, TUE, at(TUE, 18))
    flow = add_cashflow(session, 'out', Decimal('90000'), at(TUE, 19), '卖出后出金')
    session.commit()
    session.expire_all()
    assert project(session, flow.account_id, TUE).cash == Decimal('10000')
    assert create_snapshot_run(session, TUE, at(TUE, 20)).status == 'succeeded'


def test_legacy_withdrawal_is_replayed_after_close(session):
    from app.models import CashFlow
    instruments = book(session)
    equity = instruments['510300'].id
    for day in (MON, TUE):
        bar(session, equity, day, '4')
    submit_order(session, equity, 'buy', at(MON, 10), {'reason': '买'}, quantity=10000)
    fill_pending(session, MON, at(MON, 18))
    submit_order(session, equity, 'sell', at(TUE, 10), {'reason': '卖'}, quantity=10000)
    fill_pending(session, TUE, at(TUE, 18))
    session.add(CashFlow(account_id=1, direction='out', amount=Decimal('90000'), occurred_on=TUE, note='旧记录'))
    session.flush()
    assert project(session, 1, TUE).cash == Decimal('10000')


def test_post_close_deposit_cannot_fund_that_days_order(session):
    instruments = book(session)
    equity = instruments['510300'].id
    trade = submit_order(session, equity, 'buy', at(MON, 10), {'reason': '买'}, amount=Decimal('150000'))
    add_cashflow(session, 'in', Decimal('100000'), at(MON, 19), '盘后入金')
    bar(session, equity, MON, '10')
    fill_pending(session, MON, at(MON, 20))
    assert trade.status == 'rejected'
    assert project(session, 1, MON).cash == Decimal('200000')


@pytest.mark.parametrize('later_event', ['buy', 'withdrawal'])
def test_late_fill_cannot_break_confirmed_ledger(session, later_event):
    instruments = book(session)
    equity, bond = instruments['510300'].id, instruments['511010'].id
    old = submit_order(session, equity, 'buy', at(MON, 10), {'reason': '缺行情'}, amount=Decimal('100000'))
    if later_event == 'buy':
        bar(session, bond, TUE, '10')
        later = submit_order(session, bond, 'buy', at(TUE, 10), {'reason': '已成交'}, quantity=10000)
        fill_pending(session, TUE, at(TUE, 18))
        assert later.status == 'filled'
    else:
        add_cashflow(session, 'out', Decimal('90000'), at(TUE, 10), '已出金')
    bar(session, equity, MON, '10')
    fill_pending(session, TUE, at(TUE, 19))
    assert old.status == 'rejected'
    assert '后续账本' in old.reject_reason
    assert old.quantity is old.price is old.amount is old.filled_at is None
    assert project(session, 1, TUE).cash == Decimal('0' if later_event == 'buy' else '10000')
    assert create_snapshot_run(session, TUE, at(TUE, 20)).status == 'succeeded'


def test_late_fill_succeeds_when_later_ledger_remains_valid(session):
    instruments = book(session)
    equity = instruments['510300'].id
    trade = submit_order(session, equity, 'buy', at(MON, 10), {'reason': '缺行情'}, amount=Decimal('40000'))
    add_cashflow(session, 'out', Decimal('10000'), at(TUE, 10), '出金')
    bar(session, equity, MON, '10')
    fill_pending(session, TUE, at(TUE, 19))
    assert trade.status == 'filled'
    assert project(session, 1, TUE).cash == Decimal('50000')


def test_migration_adds_cashflow_time_without_losing_old_rows(monkeypatch):
    from sqlalchemy import inspect, text
    import app.db as db_module
    legacy_engine = create_engine('sqlite://')
    Base.metadata.create_all(legacy_engine)
    with legacy_engine.begin() as connection:
        connection.execute(text('ALTER TABLE cash_flows DROP COLUMN occurred_at'))
        connection.execute(text("INSERT INTO cash_flows (account_id,direction,amount,occurred_on,note) VALUES (1,'in','100','2026-10-12','legacy')"))
    monkeypatch.setattr(db_module, 'engine', legacy_engine)
    db_module.migrate()
    db_module.migrate()
    assert 'occurred_at' in {column['name'] for column in inspect(legacy_engine).get_columns('cash_flows')}
    with legacy_engine.connect() as connection:
        assert connection.execute(text('SELECT amount, occurred_at FROM cash_flows')).one() == ('100', None)


def test_rejected_cashflow_leaves_no_persisted_event(session):
    from app.models import CashFlow
    instruments = book(session)
    equity = instruments['510300'].id
    for day in (MON, TUE):
        bar(session, equity, day, '4')
    submit_order(session, equity, 'buy', at(MON, 10), {'reason': '买'}, quantity=10000)
    fill_pending(session, MON, at(MON, 18))
    submit_order(session, equity, 'sell', at(TUE, 10), {'reason': '卖'}, quantity=10000)
    fill_pending(session, TUE, at(TUE, 18))
    # 余额虽含当天卖出收入，但不能把盘前出金插入到卖出之前。
    count = session.query(CashFlow).count()
    with pytest.raises(DomainError, match='现金不足'):
        add_cashflow(session, 'out', Decimal('90000'), at(TUE, 9), '无效的盘前出金')
    session.commit()
    assert session.query(CashFlow).count() == count
    assert project(session, 1, TUE).cash == Decimal('100000')


def test_fill_includes_earlier_dated_trade_with_larger_id(session):
    from app.models import Trade
    instruments = book(session)
    equity = instruments['510300'].id
    for day in (MON, TUE):
        bar(session, equity, day, '10')
    sell = submit_order(session, equity, 'sell', at(TUE, 10), {'reason': '卖出'}, amount=Decimal('10000'))
    # 例如补生成的分红再投资：记录 ID 较大，实际交易日期却更早。
    historical_buy = Trade(account_id=1, plan_id=1, instrument_id=equity, side='buy',
        quantity_requested=1000, quantity=1000, price=Decimal('10'), amount=Decimal('10000'),
        fee=Decimal('0'), status='filled', trade_date=MON, submitted_at=at(TUE, 17),
        filled_at=at(TUE, 17), source='dividend_reinvest')
    session.add(historical_buy)
    session.flush()
    assert historical_buy.id > sell.id
    fill_pending(session, TUE, at(TUE, 18))
    assert sell.status == 'filled'
    assert project(session, 1, TUE).cash == Decimal('100000')
    assert project(session, 1, TUE).positions[equity].qty == 0
