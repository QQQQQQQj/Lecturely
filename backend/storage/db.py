"""数据库引擎、会话工厂与版本化迁移（ADR-005）。

- SQLite + WAL；启动时自动应用缺失迁移；失败拒绝启动（不静默跳过）。
- 仓储层通过 get_session() 获取异步连接；写操作集中在各 repository。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from sqlalchemy import event, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from ..errors import LecturelyError, ErrorInfo
from ..logging_setup import get_logger
from . import models

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _set_sqlite_pragma(dbapi_conn, _record):  # noqa: ANN001
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


async def init_engine(database_url: str) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine
    engine = create_async_engine(database_url, echo=False, future=True)
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragma)
    _engine = engine
    _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await run_migrations(engine)
    return engine


def get_session() -> async_sessionmaker:
    if _session_factory is None:
        raise LecturelyError(ErrorInfo(
            scope="db", code="db_not_initialized",
            message="数据库未初始化", suggestion="请先启动应用完成初始化",
        ))
    return _session_factory


async def run_migrations(engine: AsyncEngine) -> None:
    files = sorted(
        (f for f in MIGRATIONS_DIR.glob("V*.sql")),
        key=lambda f: _migration_version(f.name),
    )
    async with engine.begin() as conn:
        # 迁移记录表需先于一切迁移存在
        await conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at BIGINT NOT NULL)"
        )
        await conn.execute(
            models.schema_migrations.insert().prefix_with("OR IGNORE").values(version=0, applied_at=_now_ms())
        )
        rows = (await conn.execute(select(models.schema_migrations.c.version))).all()
        applied = {r[0] for r in rows}
        for f in files:
            version = _migration_version(f.name)
            if version in applied:
                continue
            sql = f.read_text(encoding="utf-8")
            logger.info("应用数据库迁移 V%03d：%s", version, f.name)
            try:
                for stmt in _split_sql(sql):
                    if stmt.strip():
                        await conn.exec_driver_sql(stmt)
                await conn.execute(
                    models.schema_migrations.insert().values(version=version, applied_at=_now_ms())
                )
            except Exception as e:  # noqa: BLE001
                raise LecturelyError(ErrorInfo(
                    scope="db", code="migration_failed",
                    message=f"数据库迁移 V{version:03d} 失败：{e}",
                    suggestion="请检查 data/database/lecturely.db 是否损坏，或备份后删除重试",
                )) from e


def _migration_version(filename: str) -> int:
    m = re.match(r"V(\d+)", filename)
    return int(m.group(1)) if m else 0


def _split_sql(sql: str) -> list[str]:
    # 简单按分号切分（迁移 SQL 不含存储过程/触发器内分号）
    return [s for s in sql.split(";")]


async def close_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def _now_ms() -> int:
    return int(time.time() * 1000)
