from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Dec(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return format(Decimal(value), "f")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value)


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    asset_class: Mapped[str] = mapped_column(String(16))
    exchange: Mapped[str] = mapped_column(String(8), default="SH")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    lot_size: Mapped[int] = mapped_column(Integer, default=100)
    tradable: Mapped[bool] = mapped_column(Boolean, default=True)
    dividend_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MarketBar(Base):
    __tablename__ = "market_bars"
    __table_args__ = (UniqueConstraint("instrument_id", "trade_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    trade_date: Mapped[date] = mapped_column(Date)
    close: Mapped[Decimal] = mapped_column(Dec)
    source: Mapped[str] = mapped_column(String(32), default="akshare")


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (UniqueConstraint("instrument_id", "ex_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    ex_date: Mapped[date] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cash_per_share: Mapped[Decimal] = mapped_column(Dec)
    split_ratio: Mapped[Decimal] = mapped_column(Dec, default=Decimal("1"))
    source: Mapped[str] = mapped_column(String(32), default="akshare")


class TradingDay(Base):
    __tablename__ = "trading_days"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)


class Benchmark(Base):
    __tablename__ = "benchmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="index")
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    instrument: Mapped[Instrument] = relationship()


class AllocationPlan(Base):
    __tablename__ = "allocation_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    strategy_tag: Mapped[str] = mapped_column(String(64), default="")
    benchmark_id: Mapped[int] = mapped_column(ForeignKey("benchmarks.id"))
    rebalance_threshold: Mapped[Decimal] = mapped_column(Dec, default=Decimal("0.05"))
    status: Mapped[str] = mapped_column(String(16), default="active")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    benchmark: Mapped[Benchmark] = relationship()
    versions: Mapped[list["PlanWeightVersion"]] = relationship(back_populates="plan")


class PlanWeightVersion(Base):
    __tablename__ = "plan_weight_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("allocation_plans.id"))
    effective_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    plan: Mapped[AllocationPlan] = relationship(back_populates="versions")
    assets: Mapped[list["PlanAsset"]] = relationship(back_populates="version")


class PlanAsset(Base):
    __tablename__ = "plan_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("plan_weight_versions.id"))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), nullable=True)
    asset_class: Mapped[str] = mapped_column(String(16))
    target_weight: Mapped[Decimal] = mapped_column(Dec)
    version: Mapped[PlanWeightVersion] = relationship(back_populates="assets")
    instrument: Mapped[Instrument | None] = relationship()


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    created_on: Mapped[date] = mapped_column(Date)
    base_currency: Mapped[str] = mapped_column(String(8), default="CNY")
    dividend_reinvest: Mapped[bool] = mapped_column(Boolean, default=False)
    reinvest_since: Mapped[date | None] = mapped_column(Date, nullable=True)


class PlanBinding(Base):
    __tablename__ = "plan_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("allocation_plans.id"))
    effective_on: Mapped[date] = mapped_column(Date)


class CashFlow(Base):
    __tablename__ = "cash_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    direction: Mapped[str] = mapped_column(String(8))
    amount: Mapped[Decimal] = mapped_column(Dec)
    occurred_on: Mapped[date] = mapped_column(Date)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(String(200), default="")


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    exit_condition: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    emotion_tag: Mapped[str] = mapped_column(String(16), default="")
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("allocation_plans.id"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    side: Mapped[str] = mapped_column(String(8))
    quantity_requested: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_requested: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    fee: Mapped[Decimal] = mapped_column(Dec, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    trade_date: Mapped[date] = mapped_column(Date)
    submitted_at: Mapped[datetime] = mapped_column(DateTime)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_log_id: Mapped[int | None] = mapped_column(ForeignKey("decision_logs.id"), nullable=True)
    is_temporary: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    reject_reason: Mapped[str] = mapped_column(String(200), default="")
    checklist_json: Mapped[str] = mapped_column(Text, default="")
    instrument: Mapped[Instrument] = relationship()
    log: Mapped[DecisionLog | None] = relationship()


class SnapshotRun(Base):
    __tablename__ = "snapshot_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    as_of: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str] = mapped_column(Text, default="")
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)


class DailySnapshot(Base):
    __tablename__ = "daily_snapshots"
    __table_args__ = (UniqueConstraint("run_id", "trade_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("snapshot_runs.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    trade_date: Mapped[date] = mapped_column(Date)
    total_value: Mapped[Decimal] = mapped_column(Dec)
    cash: Mapped[Decimal] = mapped_column(Dec)
    market_value: Mapped[Decimal] = mapped_column(Dec)
    net_flow: Mapped[Decimal] = mapped_column(Dec)
    daily_pnl: Mapped[Decimal] = mapped_column(Dec)
    daily_return: Mapped[Decimal] = mapped_column(Dec)
    nav: Mapped[Decimal] = mapped_column(Dec)
    cumulative_return: Mapped[Decimal] = mapped_column(Dec)
    benchmark_nav: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    lines: Mapped[list["SnapshotLine"]] = relationship(back_populates="snapshot")


class SnapshotLine(Base):
    __tablename__ = "snapshot_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("daily_snapshots.id"))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), nullable=True)
    asset_class: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[Decimal] = mapped_column(Dec, default=Decimal("0"))
    close_price: Mapped[Decimal | None] = mapped_column(Dec, nullable=True)
    market_value: Mapped[Decimal] = mapped_column(Dec)
    actual_weight: Mapped[Decimal] = mapped_column(Dec)
    target_weight: Mapped[Decimal] = mapped_column(Dec)
    price_flag: Mapped[str] = mapped_column(String(16), default="ok")
    snapshot: Mapped[DailySnapshot] = relationship(back_populates="lines")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(64))


class ClassBenchmark(Base):
    __tablename__ = "class_benchmarks"

    asset_class: Mapped[str] = mapped_column(String(16), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    instrument: Mapped[Instrument] = relationship()


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("currency", "trade_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    currency: Mapped[str] = mapped_column(String(8))
    trade_date: Mapped[date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Dec)
    source: Mapped[str] = mapped_column(String(32), default="manual")


class RebalanceTask(Base):
    __tablename__ = "rebalance_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("allocation_plans.id"))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), nullable=True)
    asset_class: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    opened_on: Mapped[date] = mapped_column(Date)
    deviation: Mapped[Decimal] = mapped_column(Dec)
    suggested_amount: Mapped[Decimal] = mapped_column(Dec)
    status: Mapped[str] = mapped_column(String(16), default="open")
    ignore_reason: Mapped[str] = mapped_column(String(200), default="")
    extra_threshold: Mapped[Decimal] = mapped_column(Dec, default=Decimal("0"))
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), nullable=True)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    rebalance_frequency: Mapped[str] = mapped_column(String(16), default="monthly")
    metrics_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
