import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from app.core.db import get_session_maker
from app.core.auth import get_current_admin, get_admin_tenant_id
from app.models.document import Document, Chunk
from app.models.chat import ChatSession, ChatMessage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/stats", tags=["stats"])

@router.get("/dashboard")
async def dashboard_stats(admin=Depends(get_current_admin)):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        def _scope(stmt, model):
            if tid:
                return stmt.where(model.tenant_id == tid)
            return stmt

        total_docs = (await db.execute(_scope(select(func.count(Document.id)), Document))).scalar() or 0
        total_chunks = (await db.execute(_scope(select(func.count(Chunk.id)), Chunk))).scalar() or 0
        total_sessions = (await db.execute(_scope(select(func.count(ChatSession.id)), ChatSession))).scalar() or 0
        total_messages = (await db.execute(
            select(func.count(ChatMessage.id)).where(
                ChatMessage.session_id.in_(
                    _scope(select(ChatSession.id), ChatSession)
                )
            ) if tid else select(func.count(ChatMessage.id))
        )).scalar() or 0

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)

        today_q = select(func.count(ChatSession.id)).where(ChatSession.created_at >= today_start)
        yesterday_q = select(func.count(ChatSession.id)).where(ChatSession.created_at >= yesterday_start, ChatSession.created_at < today_start)
        if tid:
            today_q = today_q.where(ChatSession.tenant_id == tid)
            yesterday_q = yesterday_q.where(ChatSession.tenant_id == tid)

        today_sessions = (await db.execute(today_q)).scalar() or 0
        yesterday_sessions = (await db.execute(yesterday_q)).scalar() or 0

        recent_stmt = select(ChatSession).order_by(ChatSession.created_at.desc()).limit(10)
        if tid:
            recent_stmt = recent_stmt.where(ChatSession.tenant_id == tid)
        recent_result = await db.execute(recent_stmt)
        recent_sessions = recent_result.scalars().all()

        recent_list = []
        for sess in recent_sessions:
            msg_result = await db.execute(
                select(ChatMessage).where(ChatMessage.session_id == sess.id, ChatMessage.role == "user")
                .order_by(ChatMessage.created_at.asc()).limit(1)
            )
            first_msg = msg_result.scalar_one_or_none()
            msg_count = (await db.execute(
                select(func.count(ChatMessage.id)).where(ChatMessage.session_id == sess.id)
            )).scalar() or 0

            recent_list.append({
                "session_key": sess.session_key,
                "first_message": first_msg.message[:100] if first_msg else "",
                "message_count": msg_count,
                "created_at": sess.created_at.isoformat(),
            })

        return {
            "total_documents": total_docs,
            "total_chunks": total_chunks,
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "today_sessions": today_sessions,
            "yesterday_sessions": yesterday_sessions,
            "recent_sessions": recent_list,
        }
