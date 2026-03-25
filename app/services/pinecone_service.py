"""Pinecone vector database service.

Provides lazy-initialized Pinecone index access with tenant-based namespaces.
All operations are wrapped in try/except to never crash the application.
"""

import logging
from functools import lru_cache

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_index = None


def _get_index():
    """Lazy-initialize and return the Pinecone index."""
    global _index
    if _index is None:
        try:
            from pinecone import Pinecone

            pc = Pinecone(api_key=settings.pinecone_api_key)
            _index = pc.Index(settings.pinecone_index_name)
            logger.info(
                "Pinecone index '%s' initialized", settings.pinecone_index_name
            )
        except Exception as e:
            logger.error("Failed to initialize Pinecone index: %s", e)
            raise
    return _index


def _namespace(tenant_id: str | None) -> str:
    """Map tenant_id to Pinecone namespace."""
    return tenant_id if tenant_id else "default"


class PineconeService:
    """High-level wrapper around a single Pinecone index."""

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------
    async def upsert_chunks(
        self,
        chunks: list[dict],
        tenant_id: str | None = None,
    ) -> None:
        """Upsert chunk vectors with metadata into the tenant namespace.

        Each item in *chunks* must contain:
          - id:          unique vector id (str)
          - embedding:   list[float]
          - document_id: int
          - title:       str
          - source_type: str
          - chunk_index: int
          - chunk_text:  str
        """
        try:
            index = _get_index()
            ns = _namespace(tenant_id)

            vectors = []
            for c in chunks:
                meta = {
                    "document_id": c["document_id"],
                    "title": c.get("title", ""),
                    "source_type": c.get("source_type", ""),
                    "chunk_index": c.get("chunk_index", 0),
                    "chunk_text": c.get("chunk_text", "")[:30_000],  # 30 KB cap
                }
                vectors.append(
                    {
                        "id": str(c["id"]),
                        "values": c["embedding"],
                        "metadata": meta,
                    }
                )

            # Pinecone allows max 100 vectors per upsert call
            batch_size = 100
            for i in range(0, len(vectors), batch_size):
                batch = vectors[i : i + batch_size]
                index.upsert(vectors=batch, namespace=ns)

            logger.info(
                "Upserted %d vectors to Pinecone namespace '%s'",
                len(vectors),
                ns,
            )
        except Exception as e:
            logger.error("Pinecone upsert failed: %s", e)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        tenant_id: str | None = None,
        filter: dict | None = None,
    ) -> list[dict]:
        """Query the Pinecone namespace and return matches.

        Returns list of {"id": str, "score": float, "metadata": dict}.
        """
        try:
            index = _get_index()
            ns = _namespace(tenant_id)

            results = index.query(
                vector=query_embedding,
                top_k=top_k,
                namespace=ns,
                filter=filter,
                include_metadata=True,
            )

            return [
                {
                    "id": m["id"],
                    "score": m["score"],
                    "metadata": m.get("metadata", {}),
                }
                for m in results.get("matches", [])
            ]
        except Exception as e:
            logger.error("Pinecone search failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Delete by document
    # ------------------------------------------------------------------
    async def delete_by_document(
        self,
        document_id: int,
        tenant_id: str | None = None,
    ) -> None:
        """Delete all vectors belonging to a document (metadata filter)."""
        try:
            index = _get_index()
            ns = _namespace(tenant_id)

            index.delete(
                filter={"document_id": {"$eq": document_id}},
                namespace=ns,
            )
            logger.info(
                "Deleted vectors for document_id=%d in namespace '%s'",
                document_id,
                ns,
            )
        except Exception as e:
            logger.error("Pinecone delete_by_document failed: %s", e)

    # ------------------------------------------------------------------
    # Delete namespace
    # ------------------------------------------------------------------
    async def delete_namespace(
        self,
        tenant_id: str | None = None,
    ) -> None:
        """Delete an entire Pinecone namespace."""
        try:
            index = _get_index()
            ns = _namespace(tenant_id)

            index.delete(delete_all=True, namespace=ns)
            logger.info("Deleted Pinecone namespace '%s'", ns)
        except Exception as e:
            logger.error("Pinecone delete_namespace failed: %s", e)


# ------------------------------------------------------------------
# Module-level singleton accessor
# ------------------------------------------------------------------
_service: PineconeService | None = None


def get_pinecone_service() -> PineconeService:
    global _service
    if _service is None:
        _service = PineconeService()
    return _service
