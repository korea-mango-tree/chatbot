from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

_engine = None
_async_session = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            connect_args={"ssl": False},
        )
    return _engine


def get_session_maker():
    global _async_session
    if _async_session is None:
        _async_session = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _async_session


class Base(DeclarativeBase):
    pass


async def get_db():
    async with get_session_maker()() as session:
        yield session


async def init_db():
    # 모든 모델 import해서 metadata에 등록
    import app.models.document  # noqa
    import app.models.chat  # noqa
    import app.models.document_group  # noqa
    import app.models.admin  # noqa
    import app.models.settings  # noqa
    import app.models.faq  # noqa
    import app.models.tenant  # noqa

    async with get_engine().begin() as conn:
        await conn.execute(
            __import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector")
        )
        await conn.run_sync(Base.metadata.create_all)

        # Add tsvector column for full-text search if not exists
        await conn.execute(
            __import__("sqlalchemy").text("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='chunks' AND column_name='search_vector'
                    ) THEN
                        ALTER TABLE chunks ADD COLUMN search_vector tsvector;
                    END IF;
                END $$;
            """)
        )
        await conn.execute(
            __import__("sqlalchemy").text("""
                CREATE INDEX IF NOT EXISTS idx_chunks_search_vector ON chunks USING GIN(search_vector);
            """)
        )
        # Backfill existing chunks
        await conn.execute(
            __import__("sqlalchemy").text("""
                UPDATE chunks SET search_vector = to_tsvector('simple', chunk_text) WHERE search_vector IS NULL;
            """)
        )
