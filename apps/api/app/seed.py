from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import SH, save_weight_version
from app.models import AllocationPlan, Benchmark, ClassBenchmark, Instrument


SAMPLES = [
    ("510300", "沪深300ETF", "equity", "SH", True),
    ("512890", "红利低波ETF", "equity", "SH", True),
    ("511010", "国债ETF", "bond", "SH", True),
    ("518880", "黄金ETF", "commodity", "SH", True),
    ("000300", "沪深300", "index", "SH", False),
]


def ensure_instruments(session: Session) -> None:
    for symbol, name, asset_class, exchange, tradable in SAMPLES:
        if session.scalar(select(Instrument).where(Instrument.symbol == symbol)):
            continue
        session.add(
            Instrument(
                symbol=symbol,
                name=name,
                asset_class=asset_class,
                exchange=exchange,
                currency="CNY",
                lot_size=100,
                tradable=tradable,
            )
        )
    session.flush()


def ensure_class_benchmarks(session: Session) -> None:
    by_symbol = {item.symbol: item for item in session.scalars(select(Instrument)).all()}
    defaults = {"equity": "000300", "bond": "511010", "commodity": "518880"}
    for asset_class, symbol in defaults.items():
        if session.get(ClassBenchmark, asset_class) or symbol not in by_symbol:
            continue
        session.add(ClassBenchmark(asset_class=asset_class, instrument_id=by_symbol[symbol].id))
    session.flush()


def seed(session: Session) -> None:
    ensure_instruments(session)
    ensure_class_benchmarks(session)
    if session.scalar(select(AllocationPlan.id).limit(1)):
        session.commit()
        return
    moment = datetime.now(SH)
    by_symbol = {item.symbol: item for item in session.scalars(select(Instrument)).all()}
    session.flush()
    benchmark = Benchmark(name="沪深300", kind="index", instrument_id=by_symbol["000300"].id)
    session.add(benchmark)
    session.flush()
    plan = AllocationPlan(
        name="60/30/10 股债金",
        description="股票 ETF、国债 ETF 与黄金 ETF 的经典配置，用来开始第一次练习。",
        strategy_tag="长期配置",
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
            {"instrument_id": by_symbol["510300"].id, "asset_class": "equity", "target_weight": "0.60"},
            {"instrument_id": by_symbol["511010"].id, "asset_class": "bond", "target_weight": "0.30"},
            {"instrument_id": by_symbol["518880"].id, "asset_class": "commodity", "target_weight": "0.10"},
        ],
        moment.date(),
        moment,
    )
    session.commit()
