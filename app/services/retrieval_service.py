import sqlalchemy
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.document import Chunk
from app.models.document_group import DocumentGroupMember
from app.services.embedding_service import create_embedding, create_embeddings


async def _vector_search(db: AsyncSession, query_embedding: list[float], top_k: int = 20, tenant_id: str | None = None) -> list[tuple[int, int]]:
    """Vector cosine similarity search. Returns list of (chunk_id, rank)."""
    stmt = (
        select(Chunk.id)
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    if tenant_id:
        stmt = stmt.where(Chunk.tenant_id == tenant_id)
    result = await db.execute(stmt)
    return [(row[0], idx + 1) for idx, row in enumerate(result.all())]


async def _fulltext_search(db: AsyncSession, query: str, top_k: int = 20, tenant_id: str | None = None) -> list[tuple[int, int]]:
    """PostgreSQL full-text search. Returns list of (chunk_id, rank)."""
    tenant_filter = "AND tenant_id = :tenant_id" if tenant_id else ""
    sql = text(f"""
        SELECT id, ts_rank(search_vector, plainto_tsquery('simple', :query)) as rank
        FROM chunks
        WHERE search_vector @@ plainto_tsquery('simple', :query)
        {tenant_filter}
        ORDER BY rank DESC
        LIMIT :limit
    """)
    params = {"query": query, "limit": top_k}
    if tenant_id:
        params["tenant_id"] = tenant_id
    result = await db.execute(sql, params)
    rows = result.all()
    return [(row[0], idx + 1) for idx, row in enumerate(rows)]


def _rrf_merge(vector_results: list[tuple[int, int]], fulltext_results: list[tuple[int, int]], k: int = 60) -> list[int]:
    """Reciprocal Rank Fusion to merge two ranked lists."""
    scores = {}
    for chunk_id, rank in vector_results:
        scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank)
    for chunk_id, rank in fulltext_results:
        scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank)

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return sorted_ids


def _multi_rrf_merge(ranked_lists: list[list[tuple[int, int]]], k: int = 60, vector_weight: float = 0.6, fulltext_weight: float = 0.4) -> list[int]:
    """RRF merge across N ranked lists. Last list is fulltext (lower weight), rest are vector (higher weight)."""
    scores: dict[int, float] = {}
    for i, ranked_list in enumerate(ranked_lists):
        # 마지막 리스트는 풀텍스트 (가중치 낮게), 나머지는 벡터 (가중치 높게)
        weight = fulltext_weight if i == len(ranked_lists) - 1 else vector_weight
        for rank, (chunk_id, _) in enumerate(ranked_list):
            scores[chunk_id] = scores.get(chunk_id, 0) + weight / (k + rank + 1)
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return sorted_ids


async def _get_group_related_doc_ids(db: AsyncSession, doc_ids: set[int]) -> set[int]:
    """Find all document IDs related through groups."""
    if not doc_ids:
        return doc_ids

    group_stmt = (
        select(DocumentGroupMember.group_id)
        .where(DocumentGroupMember.document_id.in_(doc_ids))
        .distinct()
    )
    group_result = await db.execute(group_stmt)
    group_ids = [row[0] for row in group_result.all()]

    if not group_ids:
        return doc_ids

    related_stmt = (
        select(DocumentGroupMember.document_id)
        .where(DocumentGroupMember.group_id.in_(group_ids))
        .distinct()
    )
    related_result = await db.execute(related_stmt)
    all_doc_ids = set(doc_ids)
    for row in related_result.all():
        all_doc_ids.add(row[0])

    return all_doc_ids


async def _expand_to_parents(db: AsyncSession, chunks: list[dict]) -> list[dict]:
    """Replace child chunk texts with their parent chunk texts for better LLM context.

    - For each chunk with parent_chunk_index in metadata, load the parent chunk
      (same document_id, is_parent=True, chunk_index==parent_chunk_index) and
      use the parent's text for LLM context.
    - Original child text is preserved in "search_text".
    - Multiple children mapping to the same parent are deduplicated.
    """
    expanded: list[dict] = []
    seen_parents: dict[tuple[int, int], dict] = {}  # (doc_id, parent_index) -> result dict

    for chunk in chunks:
        meta = chunk.get("metadata_") or {}
        parent_idx = meta.get("parent_chunk_index")

        if parent_idx is None:
            # Regular chunk (recursive strategy or legacy data) — pass through
            expanded.append(chunk)
            continue

        doc_id = chunk["document_id"]
        parent_key = (doc_id, parent_idx)

        if parent_key in seen_parents:
            # Same parent already included — skip duplicate
            continue

        # Load parent chunk
        parent_stmt = (
            select(Chunk)
            .where(
                Chunk.document_id == doc_id,
                Chunk.chunk_index == parent_idx,
            )
        )
        parent_result = await db.execute(parent_stmt)
        parent_chunk = parent_result.scalars().first()

        if parent_chunk is not None:
            parent_text = parent_chunk.chunk_text
        else:
            # Fallback: parent not found, use child text
            parent_text = chunk["chunk_text"]

        result_dict = {
            **chunk,
            "chunk_text": parent_text,
            "search_text": chunk["chunk_text"],
        }
        seen_parents[parent_key] = result_dict
        expanded.append(result_dict)

    return expanded


async def search_chunks(
    db: AsyncSession,
    query: str,
    top_k: int = 10,
    tenant_id: str | None = None,
    hyde_text: str | None = None,
    multi_queries: list[str] | None = None,
) -> list[dict]:
    # Step 1: Collect all query texts and batch-embed them
    all_query_texts = [query]
    if hyde_text:
        all_query_texts.append(hyde_text)
    if multi_queries:
        all_query_texts.extend(multi_queries)

    all_embeddings = await create_embeddings(all_query_texts)

    # Step 2: Run vector search for each embedding + fulltext for original query
    ranked_lists: list[list[tuple[int, int]]] = []

    for emb in all_embeddings:
        vr = await _vector_search(db, emb, top_k=20, tenant_id=tenant_id)
        ranked_lists.append(vr)

    fulltext_results = await _fulltext_search(db, query, top_k=20, tenant_id=tenant_id)
    ranked_lists.append(fulltext_results)

    # Step 3: Multi-way RRF merge
    merged_ids = _multi_rrf_merge(ranked_lists)

    # Step 4: Get top chunks
    top_chunk_ids = merged_ids[:top_k]

    if not top_chunk_ids:
        return []

    # Step 4: Load chunks with documents
    stmt = (
        select(Chunk)
        .options(joinedload(Chunk.document))
        .where(Chunk.id.in_(top_chunk_ids))
    )
    result = await db.execute(stmt)
    initial_chunks = result.scalars().all()
    chunk_map = {c.id: c for c in initial_chunks}

    # Step 5: Smart group expansion - find related docs
    doc_ids = set(c.document_id for c in initial_chunks)
    expanded_doc_ids = await _get_group_related_doc_ids(db, doc_ids)
    new_doc_ids = expanded_doc_ids - doc_ids

    # Step 6: For related documents, do targeted vector search (top 5 per doc)
    query_embedding = all_embeddings[0]  # original query embedding
    expanded_chunks = []
    if new_doc_ids:
        for related_doc_id in list(new_doc_ids)[:3]:  # 그룹 확장 최대 3문서
            related_stmt = (
                select(Chunk)
                .options(joinedload(Chunk.document))
                .where(Chunk.document_id == related_doc_id)
                .order_by(Chunk.embedding.cosine_distance(query_embedding))
                .limit(5)
            )
            related_result = await db.execute(related_stmt)
            expanded_chunks.extend(related_result.scalars().all())

    # Step 7: Merge all chunks, preserving order
    seen = set()
    final_chunks = []

    for cid in top_chunk_ids:
        if cid in chunk_map and cid not in seen:
            seen.add(cid)
            final_chunks.append(chunk_map[cid])

    expanded_chunks.sort(key=lambda c: (c.document_id, c.chunk_index))
    for chunk in expanded_chunks:
        if chunk.id not in seen:
            seen.add(chunk.id)
            final_chunks.append(chunk)

    results = [
        {
            "chunk_id": chunk.id,
            "chunk_text": chunk.chunk_text,
            "chunk_index": chunk.chunk_index,
            "document_id": chunk.document_id,
            "document_title": chunk.document.title,
            "source_type": chunk.document.source_type,
            "metadata_": chunk.metadata_,
        }
        for chunk in final_chunks
    ]

    # Step 8: Parent-child expansion — swap child text with parent text for LLM context
    results = await _expand_to_parents(db, results)

    return results
