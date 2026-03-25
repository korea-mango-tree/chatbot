"""Vector store abstraction layer.

Provides a unified interface over pgvector and Pinecone so the rest of the
application can remain agnostic of the backing store.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ──────────────────────────────────────────────────────────────────────
# Interface
# ──────────────────────────────────────────────────────────────────────
class VectorStoreInterface(Protocol):
    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        tenant_id: str | None,
    ) -> list[dict]:
        ...

    async def upsert(
        self,
        chunks: list[dict],
        tenant_id: str | None,
    ) -> None:
        ...

    async def delete_document(
        self,
        document_id: int,
        tenant_id: str | None,
    ) -> None:
        ...


# ──────────────────────────────────────────────────────────────────────
# pgvector implementation
# ──────────────────────────────────────────────────────────────────────
class PgVectorStore:
    """Wraps the existing pgvector search from retrieval_service._vector_search."""

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        tenant_id: str | None = None,
    ) -> list[dict]:
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        from app.core.db import get_session_maker
        from app.models.document import Chunk

        async with get_session_maker()() as db:
            stmt = (
                select(Chunk)
                .options(joinedload(Chunk.document))
                .order_by(Chunk.embedding.cosine_distance(query_embedding))
                .limit(top_k)
            )
            if tenant_id:
                stmt = stmt.where(Chunk.tenant_id == tenant_id)
            result = await db.execute(stmt)
            chunks = result.scalars().all()

            return [
                {
                    "id": str(chunk.id),
                    "score": None,  # pgvector doesn't return similarity score here
                    "metadata": {
                        "document_id": chunk.document_id,
                        "title": chunk.document.title if chunk.document else "",
                        "source_type": (
                            chunk.document.source_type if chunk.document else ""
                        ),
                        "chunk_index": chunk.chunk_index,
                        "chunk_text": chunk.chunk_text,
                    },
                }
                for chunk in chunks
            ]

    async def upsert(
        self,
        chunks: list[dict],
        tenant_id: str | None = None,
    ) -> None:
        # pgvector upserts are handled by the ORM in ingest_service,
        # so this is intentionally a no-op.
        pass

    async def delete_document(
        self,
        document_id: int,
        tenant_id: str | None = None,
    ) -> None:
        # Deletion in pgvector is handled by cascading deletes on Document,
        # so this is intentionally a no-op.
        pass


# ──────────────────────────────────────────────────────────────────────
# Pinecone implementation
# ──────────────────────────────────────────────────────────────────────
class PineconeVectorStore:
    """Wraps PineconeService to match VectorStoreInterface."""

    def __init__(self):
        from app.services.pinecone_service import get_pinecone_service

        self._pc = get_pinecone_service()

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        tenant_id: str | None = None,
    ) -> list[dict]:
        return await self._pc.search(query_embedding, top_k, tenant_id)

    async def upsert(
        self,
        chunks: list[dict],
        tenant_id: str | None = None,
    ) -> None:
        await self._pc.upsert_chunks(chunks, tenant_id)

    async def delete_document(
        self,
        document_id: int,
        tenant_id: str | None = None,
    ) -> None:
        await self._pc.delete_by_document(document_id, tenant_id)


# ──────────────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────────────
_store: VectorStoreInterface | None = None


def get_vector_store() -> VectorStoreInterface:
    """Return the configured vector store singleton.

    Returns PgVectorStore when settings.vector_store == "pgvector",
    PineconeVectorStore when "pinecone", or PgVectorStore for "both"
    (Pinecone is handled explicitly in ingest/search paths for "both").
    """
    global _store
    if _store is not None:
        return _store

    mode = settings.vector_store

    if mode == "pinecone":
        _store = PineconeVectorStore()
    else:
        # "pgvector" or "both" — primary store is pgvector
        _store = PgVectorStore()

    logger.info("Vector store initialised: mode=%s, backend=%s", mode, type(_store).__name__)
    return _store
