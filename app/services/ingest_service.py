import logging
import sqlalchemy
from dataclasses import dataclass, field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.document import Document, Chunk
from app.services.embedding_service import create_embeddings
from app.services.chunking_service import get_chunks
from app.services.data_preprocessor import (
    preprocess_content, generate_metadata, validate_quality,
    looks_like_filename, QualityReport,
)

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class IngestResult:
    id: int
    title: str
    chunk_count: int
    quality_report: QualityReport | None = None
    was_converted: bool = False
    detected_format: str = "text"
    auto_metadata: dict = field(default_factory=dict)


async def ingest_text(
    db: AsyncSession,
    title: str,
    content: str,
    source_type: str = "faq",
    metadata: dict | None = None,
    tenant_id: str | None = None,
    skip_preprocess: bool = False,
) -> IngestResult:
    """문서 인제스트 파이프라인 (전처리 포함)"""

    was_converted = False
    detected_format = "text"
    auto_meta = {}
    quality = None

    # ─── ① 자동 전처리 (형식 변환) ───
    if not skip_preprocess:
        try:
            preprocessed = await preprocess_content(content, source_type, title)
            content = preprocessed.content
            was_converted = preprocessed.was_converted
            detected_format = preprocessed.detected_format
        except Exception as e:
            logger.warning(f"전처리 실패, 원본 사용: {e}")

    # ─── ② 자동 메타데이터 생성 ───
    if not skip_preprocess:
        try:
            meta = await generate_metadata(content, title)
            auto_meta = {
                "auto_title": meta.auto_title,
                "keywords": meta.keywords,
                "summary": meta.summary,
                "category": meta.category,
            }

            # 제목이 파일명이면 자동 제목 사용
            if looks_like_filename(title) and meta.auto_title:
                title = meta.auto_title

        except Exception as e:
            logger.warning(f"메타데이터 생성 실패: {e}")

    # ─── ③ 품질 검증 ───
    if not skip_preprocess:
        try:
            quality = await validate_quality(content, tenant_id, db)
        except Exception as e:
            logger.warning(f"품질 검증 실패: {e}")

    # ─── ④ 문서 저장 ───
    merged_metadata = {**(metadata or {}), **auto_meta}
    doc = Document(
        title=title,
        content=content,
        source_type=source_type,
        metadata_=merged_metadata,
        tenant_id=tenant_id,
    )
    db.add(doc)
    await db.flush()

    # ─── ⑤ 청킹 ───
    # 키워드를 청크 텍스트에 포함 (검색 정확도 향상)
    keywords = auto_meta.get("keywords", [])
    keyword_prefix = f"[키워드: {', '.join(keywords)}]\n" if keywords else ""

    if settings.chunking_strategy == "parent_child":
        # ── Parent-Child 청킹 전략 ──
        from app.services.chunking_service import get_parent_child_chunks

        parent_texts, child_items = get_parent_child_chunks(content, source_type)

        # ⑥-a 부모 청크 저장 (임베딩 없음)
        for idx, p_text in enumerate(parent_texts):
            chunk = Chunk(
                document_id=doc.id,
                chunk_index=idx,
                chunk_text=p_text,
                embedding=None,
                tenant_id=tenant_id,
                metadata_={"is_parent": True, "keywords": keywords},
            )
            db.add(chunk)

        # ⑥-b 자식 청크 임베딩 생성
        child_texts = [c["text"] for c in child_items]
        texts_for_embedding = (
            [keyword_prefix + t for t in child_texts] if keyword_prefix else child_texts
        )
        embeddings = await create_embeddings(texts_for_embedding)

        # ⑦-b 자식 청크 저장 (임베딩 포함)
        for idx, (item, emb) in enumerate(zip(child_items, embeddings)):
            chunk = Chunk(
                document_id=doc.id,
                chunk_index=len(parent_texts) + idx,  # offset after parents
                chunk_text=item["text"],
                embedding=emb,
                tenant_id=tenant_id,
                metadata_={
                    "is_parent": False,
                    "parent_chunk_index": item["parent_index"],
                    "keywords": keywords,
                },
            )
            db.add(chunk)

        total_chunks = len(parent_texts) + len(child_items)

        await db.flush()

        # ⑧ search_vector 업데이트 (자식 청크만)
        await db.execute(
            sqlalchemy.text(
                "UPDATE chunks SET search_vector = to_tsvector('simple', chunk_text) "
                "WHERE document_id = :doc_id AND search_vector IS NULL "
                "AND (metadata->>'is_parent')::text != 'true'"
            ),
            {"doc_id": doc.id},
        )

        await db.commit()

        # ⑨ Pinecone upsert (자식 청크만)
        if settings.vector_store in ("pinecone", "both"):
            try:
                from app.services.pinecone_service import get_pinecone_service

                pc = get_pinecone_service()
                chunk_data = []
                result = await db.execute(
                    sqlalchemy.text(
                        "SELECT id, chunk_index, chunk_text FROM chunks "
                        "WHERE document_id = :doc_id "
                        "AND (metadata->>'is_parent')::text != 'true' "
                        "ORDER BY chunk_index"
                    ),
                    {"doc_id": doc.id},
                )
                rows = result.all()
                for row, emb in zip(rows, embeddings):
                    chunk_data.append(
                        {
                            "id": str(row[0]),
                            "embedding": emb,
                            "document_id": doc.id,
                            "title": title,
                            "source_type": source_type,
                            "chunk_index": row[1],
                            "chunk_text": row[2],
                        }
                    )
                await pc.upsert_chunks(chunk_data, tenant_id)
            except Exception as e:
                logger.warning(f"Pinecone upsert failed: {e}")

    else:
        # ── 기존 Recursive 청킹 전략 (기본값) ──
        texts = get_chunks(content, source_type)

        # ⑥ 임베딩 생성
        texts_for_embedding = (
            [keyword_prefix + t for t in texts] if keyword_prefix else texts
        )
        embeddings = await create_embeddings(texts_for_embedding)

        # ⑦ 청크 저장
        for idx, (text, emb) in enumerate(zip(texts, embeddings)):
            chunk = Chunk(
                document_id=doc.id,
                chunk_index=idx,
                chunk_text=text,
                embedding=emb,
                tenant_id=tenant_id,
                metadata_={"keywords": keywords} if keywords else None,
            )
            db.add(chunk)

        total_chunks = len(texts)

        await db.flush()

        # ⑧ search_vector 업데이트
        await db.execute(
            sqlalchemy.text(
                "UPDATE chunks SET search_vector = to_tsvector('simple', chunk_text) "
                "WHERE document_id = :doc_id AND search_vector IS NULL"
            ),
            {"doc_id": doc.id},
        )

        await db.commit()

        # ⑨ Pinecone upsert (if configured)
        if settings.vector_store in ("pinecone", "both"):
            try:
                from app.services.pinecone_service import get_pinecone_service

                pc = get_pinecone_service()
                chunk_data = []
                result = await db.execute(
                    sqlalchemy.text(
                        "SELECT id, chunk_index, chunk_text FROM chunks "
                        "WHERE document_id = :doc_id ORDER BY chunk_index"
                    ),
                    {"doc_id": doc.id},
                )
                rows = result.all()
                for row, emb in zip(rows, embeddings):
                    chunk_data.append(
                        {
                            "id": str(row[0]),
                            "embedding": emb,
                            "document_id": doc.id,
                            "title": title,
                            "source_type": source_type,
                            "chunk_index": row[1],
                            "chunk_text": row[2],
                        }
                    )
                await pc.upsert_chunks(chunk_data, tenant_id)
            except Exception as e:
                logger.warning(f"Pinecone upsert failed: {e}")

    return IngestResult(
        id=doc.id,
        title=title,
        chunk_count=total_chunks,
        quality_report=quality,
        was_converted=was_converted,
        detected_format=detected_format,
        auto_metadata=auto_meta,
    )
