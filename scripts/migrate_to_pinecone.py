"""Migration script: pgvector -> Pinecone

Reads all existing chunks from the database in batches, re-embeds if the
embedding dimension has changed, and upserts to Pinecone with proper
tenant-based namespaces.

Usage:
    python -m scripts.migrate_to_pinecone
"""

import asyncio
import logging
import sys

from sqlalchemy import select, func
from sqlalchemy.orm import joinedload

# Ensure the project root is importable
sys.path.insert(0, ".")

from app.core.config import get_settings
from app.core.db import get_session_maker, init_db
from app.models.document import Chunk
from app.services.pinecone_service import get_pinecone_service
from app.services.embedding_service import create_embeddings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()
BATCH_SIZE = 100


async def _count_chunks() -> int:
    async with get_session_maker()() as db:
        result = await db.execute(select(func.count(Chunk.id)))
        return result.scalar_one()


async def _needs_reembed(sample_embedding: list[float] | None) -> bool:
    """Check if existing embeddings need to be regenerated (dimension mismatch)."""
    if sample_embedding is None:
        return True
    if len(sample_embedding) != settings.embedding_dimension:
        logger.info(
            "Dimension mismatch: existing=%d, target=%d — will re-embed",
            len(sample_embedding),
            settings.embedding_dimension,
        )
        return True
    return False


async def migrate():
    logger.info("=== pgvector -> Pinecone migration ===")
    logger.info(
        "Target index: %s  |  Embedding dim: %d",
        settings.pinecone_index_name,
        settings.embedding_dimension,
    )

    await init_db()

    total = await _count_chunks()
    logger.info("Total chunks to migrate: %d", total)

    if total == 0:
        logger.info("Nothing to migrate.")
        return

    pc = get_pinecone_service()

    # Determine if re-embedding is needed by sampling the first chunk
    async with get_session_maker()() as db:
        sample = await db.execute(
            select(Chunk.embedding).limit(1)
        )
        sample_emb = sample.scalar_one_or_none()
        reembed = await _needs_reembed(
            list(sample_emb) if sample_emb is not None else None
        )

    if reembed:
        logger.info("Re-embedding enabled — will generate new embeddings")
    else:
        logger.info("Existing embeddings match target dimension — reusing them")

    migrated = 0
    offset = 0

    while offset < total:
        async with get_session_maker()() as db:
            stmt = (
                select(Chunk)
                .options(joinedload(Chunk.document))
                .order_by(Chunk.id)
                .offset(offset)
                .limit(BATCH_SIZE)
            )
            result = await db.execute(stmt)
            chunks = result.scalars().all()

            if not chunks:
                break

            # Determine embeddings
            if reembed:
                texts = [c.chunk_text for c in chunks]
                embeddings = await create_embeddings(texts)
            else:
                embeddings = [list(c.embedding) for c in chunks]

            # Build upsert payload grouped by tenant
            by_tenant: dict[str | None, list[dict]] = {}
            for chunk, emb in zip(chunks, embeddings):
                tid = chunk.tenant_id
                by_tenant.setdefault(tid, []).append(
                    {
                        "id": str(chunk.id),
                        "embedding": emb,
                        "document_id": chunk.document_id,
                        "title": chunk.document.title if chunk.document else "",
                        "source_type": (
                            chunk.document.source_type if chunk.document else ""
                        ),
                        "chunk_index": chunk.chunk_index,
                        "chunk_text": chunk.chunk_text,
                    }
                )

            for tid, chunk_data in by_tenant.items():
                await pc.upsert_chunks(chunk_data, tid)

            migrated += len(chunks)
            logger.info("Progress: %d / %d chunks migrated", migrated, total)

        offset += BATCH_SIZE

    logger.info("=== Migration complete: %d chunks migrated ===", migrated)


if __name__ == "__main__":
    asyncio.run(migrate())
