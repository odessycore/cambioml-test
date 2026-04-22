import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import text

# Use postgres env var or fallback to sqlite for local tests
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./sessions.db")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight schema evolution for dev: add new columns if missing.
        # (We don't run Alembic migrations in this demo repo.)
        if DATABASE_URL.startswith("postgresql"):
            await conn.execute(
                text(
                    """
                    ALTER TABLE sessions
                    ADD COLUMN IF NOT EXISTS container_id VARCHAR
                    """
                )
            )
        else:
            # SQLite doesn't support IF NOT EXISTS for ADD COLUMN in older versions;
            # best-effort probe then alter.
            try:
                res = await conn.execute(text("PRAGMA table_info(sessions)"))
                cols = {row[1] for row in res.fetchall()}
                if "container_id" not in cols:
                    await conn.execute(text("ALTER TABLE sessions ADD COLUMN container_id TEXT"))
            except Exception:
                pass
