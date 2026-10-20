from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.domain import SH, create_account, create_snapshot_run, fill_pending, save_weight_version, submit_order
from app.habits import (
    access_granted,
    apply_stress,
    clear_password,
    compare_plans,
    confirm_checklist,
    login,
    month_calendar,
    period_report,
    set_password,
)
from app.models import AllocationPlan, Benchmark, DailySnapshot, Instrument, MarketBar, Trade

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
MON = date(2026, 10, 12)
TUE = date(2026, 10, 13)


def at(day: date, hour: int):
    return __import__("datetime").datetime(day.year, day.month, day.day, hour, 0, tzinfo=SH)


@pytest.fixture()
def session():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    yield db
    db.close()


def instrument(session, symbol, asset_class, tradable=True):
    item = Instrument(symbol=symbol, name=symbol, asset_class=asset_class, exchange="SH", currency="CNY", lot_size=100, tradable=tradable)
    session.add(item)
    session.flush()
    return item


def plan_with_account(session, equity, weights):
    moment = at(MON, 9)
    index = instrument(session, "000300", "index", tradable=False)
    benchmark = Benchmark(name="沪深300", kind="index", instrument_id=index.id)
    session.add(benchmark)
    session.flush()
    plan = AllocationPlan(
        name="主方案",
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
    save_weight_version(session, plan, weights, MON, moment)
    create_account(session, "练习", Decimal("100000"), plan.id, moment)
    return plan


def test_monthly_report_uses_snapshot_nav(session):
    equity = instrument(session, "510300", "equity")
    plan_with_account(session, equity, [{"instrument_id": equity.id, "asset_class": "equity", "target_weight": "1"}])
    session.add(MarketBar(instrument_id=equity.id, trade_date=MON, close=Decimal("4"), source="test"))
    session.add(MarketBar(instrument_id=equity.id, trade_date=TUE, close=Decimal("4.4"), source="test"))
    submit_order(session, equity.id, "buy", at(MON, 10), {"reason": "建仓"}, quantity=10000)
    fill_pending(session, MON, at(MON, 18))
    create_snapshot_run(session, TUE, at(TUE, 18))
    report = period_report(session, "month", MON)
    assert report["snapshot_days"] == 2
    assert report["portfolio_return"] > 0
    assert report["trades"][0]["name"] == "510300"
    assert report["max_drawdown"] is not None


def test_plan_compare_does_not_switch_the_live_plan(session):
    equity = instrument(session, "510300", "equity")
    bond = instrument(session, "511010", "bond")
    moment = at(MON, 9)
    index = instrument(session, "000300", "index", tradable=False)
    benchmark = Benchmark(name="沪深300", kind="index", instrument_id=index.id)
    session.add(benchmark)
    session.flush()
    left = AllocationPlan(name="股", description="", strategy_tag="", benchmark_id=benchmark.id, rebalance_threshold=Decimal("0.05"), status="active", is_primary=True, created_at=moment)
    right = AllocationPlan(name="债", description="", strategy_tag="", benchmark_id=benchmark.id, rebalance_threshold=Decimal("0.05"), status="active", is_primary=False, created_at=moment)
    session.add_all([left, right])
    session.flush()
    save_weight_version(session, left, [{"instrument_id": equity.id, "asset_class": "equity", "target_weight": "1"}], MON, moment)
    save_weight_version(session, right, [{"instrument_id": bond.id, "asset_class": "bond", "target_weight": "1"}], MON, moment)
    result = compare_plans(session, left.id, right.id, MON)
    assert result["left"]["primary"] is True
    assert result["right"]["primary"] is False
    assert session.get(AllocationPlan, left.id).is_primary is True
    names = {row["name"]: row["delta"] for row in result["rows"]}
    assert names["510300"] == Decimal("-1")
    assert names["511010"] == Decimal("1")


def test_checklist_blocks_until_every_item_is_checked(session):
    equity = instrument(session, "510300", "equity")
    plan_with_account(session, equity, [{"instrument_id": equity.id, "asset_class": "equity", "target_weight": "1"}])
    with pytest.raises(Exception, match="交易前检查"):
        confirm_checklist(session, ["direction"])
    confirmed = confirm_checklist(session, ["direction", "capacity", "reason", "price"])
    trade = submit_order(session, equity.id, "buy", at(MON, 10), {"reason": "建仓"}, quantity=100, checklist_json=confirmed)
    assert "收盘价" in trade.checklist_json


def test_calendar_marks_orders_and_weekends(session):
    equity = instrument(session, "510300", "equity")
    plan_with_account(session, equity, [{"instrument_id": equity.id, "asset_class": "equity", "target_weight": "1"}])
    submit_order(session, equity.id, "buy", at(MON, 10), {"reason": "建仓"}, quantity=100)
    payload = month_calendar(session, 2026, 10)
    monday = next(day for day in payload["days"] if day["date"] == MON)
    saturday = next(day for day in payload["days"] if day["date"] == MON + timedelta(days=5))
    assert monday["trading"] is True
    assert any(event["kind"] == "trade" for event in monday["events"])
    assert saturday["trading"] is False


def test_stress_replays_history_without_touching_the_ledger(session):
    equity = instrument(session, "510300", "equity")
    plan_with_account(session, equity, [{"instrument_id": equity.id, "asset_class": "equity", "target_weight": "1"}])
    session.add(MarketBar(instrument_id=equity.id, trade_date=MON, close=Decimal("10"), source="test"))
    session.add(MarketBar(instrument_id=equity.id, trade_date=TUE, close=Decimal("9"), source="test"))
    submit_order(session, equity.id, "buy", at(MON, 10), {"reason": "建仓"}, quantity=1000)
    fill_pending(session, MON, at(MON, 18))
    before = session.scalar(select(func.count(Trade.id)))
    result = apply_stress(session, MON, TUE, "自定义")
    assert session.scalar(select(func.count(Trade.id))) == before
    assert session.scalar(select(func.count(DailySnapshot.id))) == 0
    held = next(row for row in result["rows"] if row["name"] == "510300")
    assert held["covered"] is True
    assert held["shock"] == Decimal("-0.1")
    assert "历史情景不代表未来" in result["disclaimer"]
    uncovered = apply_stress(session, date(2015, 6, 12), date(2015, 8, 26), "2015 年股灾")
    assert any(row["source"] == "行情未覆盖" for row in uncovered["rows"])


def test_password_stays_open_until_one_is_set(session):
    assert access_granted(session, None) is True
    set_password(session, "1234")
    assert access_granted(session, None) is False
    with pytest.raises(Exception, match="密码不正确"):
        login(session, "nope")
    token = login(session, "1234")
    assert access_granted(session, token) is True
    clear_password(session, "1234")
    assert access_granted(session, None) is True
