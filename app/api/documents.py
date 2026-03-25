import logging
import traceback

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, delete

from app.core.db import get_session_maker
from app.core.auth import get_current_admin, get_admin_tenant_id
from app.models.document import Document, Chunk

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


# ── Schemas ──

class DocumentItem(BaseModel):
    id: int
    title: str
    source_type: str
    content: str
    chunk_count: int
    created_at: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentItem]
    total: int


class DocumentUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    source_type: str | None = None


class DocumentDetailResponse(BaseModel):
    id: int
    title: str
    source_type: str
    content: str
    metadata: dict | None
    chunk_count: int
    created_at: str


class BatchDeleteRequest(BaseModel):
    document_ids: list[int]


# ── List ──

@router.get("", response_model=DocumentListResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source_type: str = Query("", description="유형 필터"),
    search: str = Query("", description="제목 검색"),
    admin=Depends(get_current_admin),
):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        base = select(Document)
        if tid:
            base = base.where(Document.tenant_id == tid)
        if source_type:
            base = base.where(Document.source_type == source_type)
        if search:
            base = base.where(Document.title.ilike(f"%{search}%"))

        # 총 건수
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        # 페이징 + 청크 수 (base 쿼리의 필터를 재사용)
        stmt = (
            select(Document, func.count(Chunk.id).label("chunk_count"))
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .where(Document.id.in_(select(base.subquery().c.id)))
            .group_by(Document.id)
            .order_by(Document.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        result = await db.execute(stmt)
        rows = result.all()

        documents = [
            DocumentItem(
                id=doc.id,
                title=doc.title,
                source_type=doc.source_type,
                content=doc.content[:200] + ("..." if len(doc.content) > 200 else ""),
                chunk_count=count,
                created_at=doc.created_at.isoformat(),
            )
            for doc, count in rows
        ]
        return DocumentListResponse(documents=documents, total=total)


# ── Batch Delete (before /{doc_id} to avoid route conflict) ──

@router.post("/batch-delete")
async def batch_delete_documents(body: BatchDeleteRequest, admin=Depends(get_current_admin)):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        stmt = select(Document).where(Document.id.in_(body.document_ids))
        if tid:
            stmt = stmt.where(Document.tenant_id == tid)
        result = await db.execute(stmt)
        docs = result.scalars().all()

        for doc in docs:
            await db.delete(doc)

        await db.commit()
        return {"message": f"{len(docs)}건 삭제 완료", "deleted_count": len(docs)}


# ── Delete All ──

@router.delete("/all")
async def delete_all_documents(admin=Depends(get_current_admin)):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        if tid:
            # 테넌트 소속 문서만 삭제
            doc_ids = (await db.execute(select(Document.id).where(Document.tenant_id == tid))).scalars().all()
            if doc_ids:
                await db.execute(delete(Chunk).where(Chunk.document_id.in_(doc_ids)))
                result = await db.execute(delete(Document).where(Document.tenant_id == tid))
                await db.commit()
                return {"message": "전체 삭제 완료", "deleted_count": result.rowcount}
            return {"message": "삭제할 문서가 없습니다.", "deleted_count": 0}
        else:
            await db.execute(delete(Chunk))
            result = await db.execute(delete(Document))
            await db.commit()
            return {"message": "전체 삭제 완료", "deleted_count": result.rowcount}


# ── Chunk Inspector ──

@router.get("/{doc_id}/chunks")
async def get_document_chunks(doc_id: int, admin=Depends(get_current_admin)):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        doc = await db.get(Document, doc_id)
        if doc is None or (tid and doc.tenant_id != tid):
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

        result = await db.execute(
            select(Chunk).where(Chunk.document_id == doc_id).order_by(Chunk.chunk_index)
        )
        chunks = result.scalars().all()
        return {
            "document_id": doc_id,
            "title": doc.title,
            "chunks": [
                {"id": c.id, "chunk_index": c.chunk_index, "chunk_text": c.chunk_text}
                for c in chunks
            ],
            "total": len(chunks),
        }


# ── Rechunk ──

@router.post("/{doc_id}/rechunk")
async def rechunk_document(doc_id: int, admin=Depends(get_current_admin)):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        doc = await db.get(Document, doc_id)
        if doc is None or (tid and doc.tenant_id != tid):
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

        await db.execute(delete(Chunk).where(Chunk.document_id == doc_id))
        await db.flush()

        from app.services.embedding_service import create_embeddings
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from app.core.config import get_settings

        settings = get_settings()
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        texts = splitter.split_text(doc.content)
        embeddings = await create_embeddings(texts)

        for idx, (text, emb) in enumerate(zip(texts, embeddings)):
            db.add(Chunk(document_id=doc_id, chunk_index=idx, chunk_text=text, embedding=emb, tenant_id=doc.tenant_id))

        await db.commit()
        return {"message": "재청킹 완료", "document_id": doc_id, "chunk_count": len(texts)}


# ── Get ──

@router.get("/{doc_id}", response_model=DocumentDetailResponse)
async def get_document(doc_id: int, admin=Depends(get_current_admin)):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        stmt = (
            select(Document, func.count(Chunk.id).label("chunk_count"))
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .where(Document.id == doc_id)
            .group_by(Document.id)
        )
        result = await db.execute(stmt)
        row = result.first()

        if row is None:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

        doc, chunk_count = row
        if tid and doc.tenant_id != tid:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

        return DocumentDetailResponse(
            id=doc.id,
            title=doc.title,
            source_type=doc.source_type,
            content=doc.content,
            metadata=doc.metadata_,
            chunk_count=chunk_count,
            created_at=doc.created_at.isoformat(),
        )


# ── Update ──

@router.put("/{doc_id}", response_model=DocumentDetailResponse)
async def update_document(doc_id: int, body: DocumentUpdateRequest, admin=Depends(get_current_admin)):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        try:
            doc = await db.get(Document, doc_id)
            if doc is None or (tid and doc.tenant_id != tid):
                raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

            content_changed = False
            if body.title is not None:
                doc.title = body.title
            if body.source_type is not None:
                doc.source_type = body.source_type
            if body.content is not None and body.content != doc.content:
                doc.content = body.content
                content_changed = True

            if content_changed:
                await db.execute(delete(Chunk).where(Chunk.document_id == doc_id))
                await db.flush()

                from app.services.embedding_service import create_embeddings
                from langchain_text_splitters import RecursiveCharacterTextSplitter
                from app.core.config import get_settings

                settings = get_settings()
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=settings.chunk_size,
                    chunk_overlap=settings.chunk_overlap,
                    separators=["\n\n", "\n", ". ", " ", ""],
                )
                texts = splitter.split_text(body.content)
                embeddings = await create_embeddings(texts)

                for idx, (text, emb) in enumerate(zip(texts, embeddings)):
                    db.add(Chunk(document_id=doc_id, chunk_index=idx, chunk_text=text, embedding=emb, tenant_id=doc.tenant_id))

            await db.commit()

            chunk_count_result = await db.execute(
                select(func.count(Chunk.id)).where(Chunk.document_id == doc_id)
            )
            chunk_count = chunk_count_result.scalar()

            return DocumentDetailResponse(
                id=doc.id,
                title=doc.title,
                source_type=doc.source_type,
                content=doc.content,
                metadata=doc.metadata_,
                chunk_count=chunk_count,
                created_at=doc.created_at.isoformat(),
            )
        except HTTPException:
            raise
        except Exception:
            logger.error(traceback.format_exc())
            raise


# ── Delete Single ──

@router.delete("/{doc_id}")
async def delete_document(doc_id: int, admin=Depends(get_current_admin)):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        doc = await db.get(Document, doc_id)
        if doc is None or (tid and doc.tenant_id != tid):
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

        await db.delete(doc)
        await db.commit()
        return {"message": "삭제 완료", "document_id": doc_id}
