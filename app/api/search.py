import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.core.db import get_session_maker
from app.core.auth import get_current_admin, get_admin_tenant_id
from app.services.retrieval_service import search_chunks
from app.services.embedding_service import create_embedding

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])

class SearchTestRequest(BaseModel):
    query: str
    top_k: int = 10

@router.post("/test")
async def search_test(body: SearchTestRequest, admin=Depends(get_current_admin)):
    async with get_session_maker()() as db:
        tid = get_admin_tenant_id(admin)
        chunks = await search_chunks(db, body.query, top_k=body.top_k, tenant_id=tid)
        return {
            "query": body.query,
            "total_chunks": len(chunks),
            "chunks": chunks,
        }
