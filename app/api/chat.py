import logging
import traceback

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.core.db import get_session_maker
from app.core.tenant import check_tenant_active, get_default_tenant_id
from app.models.chat import ChatSession, ChatMessage
from app.graphs.chat_graph import chat_graph, ChatState

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    session_id: str
    message: str


class SourceItem(BaseModel):
    title: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceItem]
    answerable: bool = True


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(body: ChatRequest):
    async with get_session_maker()() as db:
        try:
            # 1. 세션 조회 또는 생성
            result = await db.execute(
                select(ChatSession).where(ChatSession.session_key == body.session_id)
            )
            session = result.scalar_one_or_none()
            if session is None:
                default_tid = await get_default_tenant_id()
                session = ChatSession(session_key=body.session_id, tenant_id=default_tid)
                db.add(session)
                await db.flush()

            # 테넌트 상태 확인 (suspended/deleted면 차단)
            await check_tenant_active(session.tenant_id)

            # 2. 사용자 메시지 저장
            user_msg = ChatMessage(session_id=session.id, role="user", message=body.message)
            db.add(user_msg)
            await db.flush()

            # 2.5. Load last 10 messages (5 pairs) for context — excluding the just-saved message
            try:
                history_result = await db.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.session_id == session.id,
                        ChatMessage.id != user_msg.id,
                    )
                    .order_by(ChatMessage.id.desc())
                    .limit(10)
                )
                history_msgs = history_result.scalars().all()
                chat_history = [
                    {"role": m.role, "message": m.message}
                    for m in reversed(history_msgs)
                ]
            except Exception:
                chat_history = []

            # 3. LangGraph 실행
            initial_state = ChatState(
                question=body.message,
                chat_history=chat_history,
                db=db,
                tenant_id=session.tenant_id,
            )
            final_state = await chat_graph.ainvoke(initial_state)

            answer = final_state["answer"]
            sources = final_state["sources"]
            answerable = final_state.get("answerable", True)

            # 4. AI 응답 저장
            ai_msg = ChatMessage(
                session_id=session.id,
                role="assistant",
                message=answer,
                retrieval_meta={"sources": sources, "answerable": answerable},
            )
            db.add(ai_msg)
            await db.commit()

            # sources를 SourceItem 리스트로 변환
            source_items = []
            for s in (sources or []):
                if isinstance(s, str):
                    source_items.append(SourceItem(title=s))
                elif isinstance(s, dict):
                    source_items.append(SourceItem(title=s.get("title", str(s))))
                else:
                    source_items.append(SourceItem(title=str(s)))

            return ChatResponse(answer=answer, sources=source_items, answerable=answerable)
        except Exception:
            logger.error(traceback.format_exc())
            raise
