import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_here = Path(__file__).resolve()
# 本机是仓库根目录；镜像里代码在 /srv/app，没有那么多层父目录。
ROOT = _here.parents[3] if len(_here.parents) > 3 else _here.parents[1]
DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("SANDBOX_DB", DATA / "sandbox.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def migrate() -> None:
    """给已经存在的练习库补上第二阶段的列。新库由 create_all 建全。"""
    from sqlalchemy import inspect, text

    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    if "trades" not in inspector.get_table_names():
        return
    with engine.begin() as conn:
        trade_cols = {column["name"] for column in inspector.get_columns("trades")}
        if "fee" not in trade_cols:
            conn.execute(text("ALTER TABLE trades ADD COLUMN fee VARCHAR DEFAULT '0'"))
        account_cols = {column["name"] for column in inspector.get_columns("accounts")}
        if "dividend_reinvest" not in account_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN dividend_reinvest BOOLEAN DEFAULT 0"))
        if "reinvest_since" not in account_cols:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN reinvest_since DATE"))
        action_cols = {column["name"] for column in inspector.get_columns("corporate_actions")}
        if "split_ratio" not in action_cols:
            conn.execute(text("ALTER TABLE corporate_actions ADD COLUMN split_ratio VARCHAR DEFAULT '1'"))
        if "checklist_json" not in trade_cols:
            conn.execute(text("ALTER TABLE trades ADD COLUMN checklist_json TEXT DEFAULT ''"))
        flow_cols = {column["name"] for column in inspector.get_columns("cash_flows")}
        if "occurred_at" not in flow_cols:
            conn.execute(text("ALTER TABLE cash_flows ADD COLUMN occurred_at DATETIME"))
