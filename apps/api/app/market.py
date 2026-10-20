"""行情适配器。AKShare 只在同步时导入，测试不依赖外网。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import SH, DomainError, the_account
from app.models import CorporateAction, Instrument, MarketBar, TradingDay


def parse_day(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10].replace("/", "-").replace(".", "-")
    return date.fromisoformat(text)


def _column(frame, *names: str):
    for name in names:
        if name in frame.columns:
            return name
    for column in frame.columns:
        for name in names:
            if name in str(column):
                return column
    return None


def sina_symbol(instrument: Instrument) -> str:
    prefix = "sh" if instrument.exchange.upper() == "SH" else "sz"
    return f"{prefix}{instrument.symbol}"


def sync_all(session: Session, start: date | None = None, end: date | None = None) -> dict:
    moment = datetime.now(SH)
    end = end or moment.date()
    account = the_account(session)
    if start is None:
        start = (account.created_on if account else end) - timedelta(days=400)
    messages: list[str] = []
    try:
        import akshare as ak
    except Exception as exc:
        raise DomainError(f"行情库不可用：{exc}") from exc

    try:
        calendar = ak.tool_trade_date_hist_sina()
        column = _column(calendar, "trade_date", "日期")
        count = 0
        for value in calendar[column].tolist():
            day = parse_day(value)
            if session.get(TradingDay, day) is None:
                session.add(TradingDay(trade_date=day))
                count += 1
        session.flush()
        messages.append(f"交易日历新增 {count} 天")
    except Exception as exc:
        messages.append(f"交易日历同步失败：{exc}")

    instruments = session.scalars(select(Instrument)).all()
    for instrument in instruments:
        try:
            bars = _fetch_bars(ak, instrument, start, end)
            saved = _upsert_bars(session, instrument.id, bars)
            messages.append(f"{instrument.symbol} 行情 {saved} 条")
        except Exception as exc:
            messages.append(f"{instrument.symbol} 行情失败：{exc}")
        if not instrument.tradable:
            continue
        try:
            actions = _fetch_dividends(ak, instrument)
            saved = _upsert_dividends(session, instrument, actions, moment)
            messages.append(f"{instrument.symbol} 分红 {saved} 条")
        except Exception as exc:
            messages.append(f"{instrument.symbol} 分红失败：{exc}")
    session.flush()
    return {"messages": messages}


def _fetch_bars(ak, instrument: Instrument, start: date, end: date):
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    if instrument.asset_class == "index":
        frame = ak.index_zh_a_hist(symbol=instrument.symbol, period="daily", start_date=start_s, end_date=end_s)
    elif instrument.symbol.startswith(("15", "16", "18", "50", "51", "52", "56", "58")):
        frame = ak.fund_etf_hist_em(symbol=instrument.symbol, period="daily", start_date=start_s, end_date=end_s, adjust="")
    else:
        frame = ak.stock_zh_a_hist(symbol=instrument.symbol, period="daily", start_date=start_s, end_date=end_s, adjust="")
    day_col = _column(frame, "日期", "date")
    close_col = _column(frame, "收盘", "close")
    if day_col is None or close_col is None:
        raise DomainError(f"无法识别行情字段：{list(frame.columns)}")
    rows = []
    for _, row in frame.iterrows():
        close = row[close_col]
        if close is None or str(close) in {"nan", "None", ""}:
            continue
        rows.append((parse_day(row[day_col]), Decimal(str(close))))
    return rows


def _upsert_bars(session: Session, instrument_id: int, rows: list[tuple[date, Decimal]]) -> int:
    saved = 0
    for trade_date, close in rows:
        existing = session.scalar(
            select(MarketBar).where(MarketBar.instrument_id == instrument_id, MarketBar.trade_date == trade_date)
        )
        if existing:
            existing.close = close
        else:
            session.add(MarketBar(instrument_id=instrument_id, trade_date=trade_date, close=close, source="akshare"))
            saved += 1
    return saved


def cash_events(rows: list[tuple[date, Decimal]], cumulative: bool) -> list[tuple[date, Decimal]]:
    """新浪 ETF 接口给的是累计分红，要换成当期每份派息。"""
    ordered = sorted(rows, key=lambda item: item[0])
    events: list[tuple[date, Decimal]] = []
    previous = Decimal("0")
    for day, value in ordered:
        amount = value - previous if cumulative else value
        if cumulative:
            previous = value
        if amount > 0:
            events.append((day, amount))
    return events


def _fetch_dividends(ak, instrument: Instrument):
    frame = ak.fund_etf_dividend_sina(symbol=sina_symbol(instrument))
    if frame is None or len(frame) == 0:
        return []
    day_col = _column(frame, "除息日期", "除息日", "日期")
    cash_col = _column(frame, "每股分红", "每份分红", "派息", "分红")
    record_col = _column(frame, "权益登记日", "登记日")
    if day_col is None or cash_col is None:
        raise DomainError(f"无法识别分红字段：{list(frame.columns)}")
    parsed = []
    for _, row in frame.iterrows():
        parsed.append((parse_day(row[day_col]), Decimal(str(row[cash_col]))))
    cumulative = "累计" in str(cash_col)
    events = cash_events(parsed, cumulative)
    record_by_day = {}
    if record_col:
        for _, row in frame.iterrows():
            record_by_day[parse_day(row[day_col])] = parse_day(row[record_col])
    return [(day, record_by_day.get(day), amount) for day, amount in events]


def _upsert_dividends(session: Session, instrument: Instrument, rows, moment: datetime) -> int:
    saved = 0
    for ex_date, record_date, cash in rows:
        existing = session.scalar(
            select(CorporateAction).where(
                CorporateAction.instrument_id == instrument.id,
                CorporateAction.ex_date == ex_date,
            )
        )
        if existing:
            existing.cash_per_share = cash
            existing.record_date = record_date
        else:
            session.add(
                CorporateAction(
                    instrument_id=instrument.id,
                    ex_date=ex_date,
                    record_date=record_date,
                    cash_per_share=cash,
                    source="akshare",
                )
            )
            saved += 1
    instrument.dividend_synced_at = moment
    return saved
