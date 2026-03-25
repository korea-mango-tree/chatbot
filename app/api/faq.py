import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.core.db import get_session_maker
from app.core.auth import get_current_admin
from app.models.faq import FaqTemplate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/faq", tags=["faq"])

class FaqCreateRequest(BaseModel):
    title: str
    content: str
    category: str | None = None

class FaqUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    category: str | None = None

class FaqItem(BaseModel):
    id: int
    title: str
    content: str
    category: str | None
    created_at: str

@router.get("")
async def list_faq(admin=Depends(get_current_admin)):
    async with get_session_maker()() as db:
        result = await db.execute(select(FaqTemplate).order_by(FaqTemplate.created_at.desc()))
        faqs = result.scalars().all()
        return {
            "faqs": [
                FaqItem(id=f.id, title=f.title, content=f.content, category=f.category, created_at=f.created_at.isoformat())
                for f in faqs
            ],
            "total": len(faqs),
        }

@router.post("", response_model=FaqItem)
async def create_faq(body: FaqCreateRequest, admin=Depends(get_current_admin)):
    async with get_session_maker()() as db:
        faq = FaqTemplate(title=body.title, content=body.content, category=body.category)
        db.add(faq)
        await db.commit()
        await db.refresh(faq)
        return FaqItem(id=faq.id, title=faq.title, content=faq.content, category=faq.category, created_at=faq.created_at.isoformat())

@router.put("/{faq_id}", response_model=FaqItem)
async def update_faq(faq_id: int, body: FaqUpdateRequest, admin=Depends(get_current_admin)):
    async with get_session_maker()() as db:
        faq = await db.get(FaqTemplate, faq_id)
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ를 찾을 수 없습니다.")
        if body.title is not None:
            faq.title = body.title
        if body.content is not None:
            faq.content = body.content
        if body.category is not None:
            faq.category = body.category
        await db.commit()
        await db.refresh(faq)
        return FaqItem(id=faq.id, title=faq.title, content=faq.content, category=faq.category, created_at=faq.created_at.isoformat())

@router.delete("/{faq_id}")
async def delete_faq(faq_id: int, admin=Depends(get_current_admin)):
    async with get_session_maker()() as db:
        faq = await db.get(FaqTemplate, faq_id)
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ를 찾을 수 없습니다.")
        await db.delete(faq)
        await db.commit()
        return {"message": "삭제 완료"}
