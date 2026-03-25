import logging
import traceback

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select, delete

from app.core.db import get_session_maker
from app.core.auth import get_current_admin, get_admin_tenant_id
from app.models.document import Document
from app.models.document_group import DocumentGroup, DocumentGroupMember

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/groups", tags=["groups"])


# ── Schemas ──

class GroupCreateRequest(BaseModel):
    name: str
    description: str | None = None
    document_ids: list[int] = []


class GroupUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class GroupMemberRequest(BaseModel):
    document_ids: list[int]


class GroupDocItem(BaseModel):
    id: int
    title: str
    source_type: str


class GroupItem(BaseModel):
    id: int
    name: str
    description: str | None
    documents: list[GroupDocItem]
    created_at: str


class GroupListResponse(BaseModel):
    groups: list[GroupItem]
    total: int


# ── helpers ──

async def _build_group_item(db, group: DocumentGroup) -> GroupItem:
    stmt = (
        select(Document)
        .join(DocumentGroupMember, DocumentGroupMember.document_id == Document.id)
        .where(DocumentGroupMember.group_id == group.id)
        .order_by(Document.id)
    )
    result = await db.execute(stmt)
    docs = result.scalars().all()

    return GroupItem(
        id=group.id,
        name=group.name,
        description=group.description,
        documents=[GroupDocItem(id=d.id, title=d.title, source_type=d.source_type) for d in docs],
        created_at=group.created_at.isoformat(),
    )


# ── List ──

@router.get("", response_model=GroupListResponse)
async def list_groups(admin=Depends(get_current_admin)):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        stmt = select(DocumentGroup).order_by(DocumentGroup.created_at.desc())
        if tid:
            stmt = stmt.where(DocumentGroup.tenant_id == tid)
        result = await db.execute(stmt)
        groups = result.scalars().all()

        items = []
        for group in groups:
            items.append(await _build_group_item(db, group))

        return GroupListResponse(groups=items, total=len(items))


# ── Create ──

@router.post("", response_model=GroupItem)
async def create_group(body: GroupCreateRequest, admin=Depends(get_current_admin)):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        try:
            group = DocumentGroup(name=body.name, description=body.description, tenant_id=tid)
            db.add(group)
            await db.flush()

            for doc_id in body.document_ids:
                db.add(DocumentGroupMember(group_id=group.id, document_id=doc_id))

            await db.commit()
            await db.refresh(group)
            return await _build_group_item(db, group)
        except Exception:
            logger.error(traceback.format_exc())
            raise


# ── Update ──

@router.put("/{group_id}", response_model=GroupItem)
async def update_group(group_id: int, body: GroupUpdateRequest):
    async with get_session_maker()() as db:
        group = await db.get(DocumentGroup, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="그룹을 찾을 수 없습니다.")

        if body.name is not None:
            group.name = body.name
        if body.description is not None:
            group.description = body.description

        await db.commit()
        await db.refresh(group)
        return await _build_group_item(db, group)


# ── Delete ──

@router.delete("/{group_id}")
async def delete_group(group_id: int):
    async with get_session_maker()() as db:
        group = await db.get(DocumentGroup, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="그룹을 찾을 수 없습니다.")

        await db.delete(group)
        await db.commit()
        return {"message": "그룹 삭제 완료", "group_id": group_id}


# ── Add documents to group ──

@router.post("/{group_id}/documents", response_model=GroupItem)
async def add_documents_to_group(group_id: int, body: GroupMemberRequest):
    async with get_session_maker()() as db:
        group = await db.get(DocumentGroup, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="그룹을 찾을 수 없습니다.")

        # 이미 있는 문서 확인
        existing = await db.execute(
            select(DocumentGroupMember.document_id).where(DocumentGroupMember.group_id == group_id)
        )
        existing_ids = {row[0] for row in existing.all()}

        for doc_id in body.document_ids:
            if doc_id not in existing_ids:
                db.add(DocumentGroupMember(group_id=group_id, document_id=doc_id))

        await db.commit()
        return await _build_group_item(db, group)


# ── Remove documents from group ──

@router.delete("/{group_id}/documents", response_model=GroupItem)
async def remove_documents_from_group(group_id: int, body: GroupMemberRequest):
    async with get_session_maker()() as db:
        group = await db.get(DocumentGroup, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="그룹을 찾을 수 없습니다.")

        await db.execute(
            delete(DocumentGroupMember).where(
                DocumentGroupMember.group_id == group_id,
                DocumentGroupMember.document_id.in_(body.document_ids),
            )
        )
        await db.commit()
        return await _build_group_item(db, group)
