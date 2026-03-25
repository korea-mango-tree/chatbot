import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, update
from app.core.db import get_session_maker
from app.core.auth import get_current_admin, get_admin_tenant_id
from app.models.chat import ChatSession, ChatMessage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


async def _get_session_with_tenant_check(db, session_key: str, admin) -> ChatSession:
    """세션 조회 + 테넌트 소유권 검증"""
    result = await db.execute(select(ChatSession).where(ChatSession.session_key == session_key))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    tid = get_admin_tenant_id(admin)
    if tid and session.tenant_id != tid:
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")
    return session

@router.get("")
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query("", description="필터: active, delayed, closed"),
    has_customer_info: bool = Query(False, description="고객정보가 있는 세션만"),
    admin=Depends(get_current_admin),
):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        # 자동 상담지연 처리: active 상태에서 마지막 메시지가 24시간 이상 지난 세션
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        delayed_stmt = select(ChatSession).where(ChatSession.status == "active")
        if tid:
            delayed_stmt = delayed_stmt.where(ChatSession.tenant_id == tid)
        active_sessions_result = await db.execute(delayed_stmt)
        for sess in active_sessions_result.scalars().all():
            last_msg_result = await db.execute(
                select(ChatMessage.created_at)
                .where(ChatMessage.session_id == sess.id)
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
            last_msg_time = last_msg_result.scalar_one_or_none()
            if last_msg_time and last_msg_time < cutoff:
                sess.status = "delayed"
        await db.commit()

        # 필터 적용
        base_query = select(ChatSession)
        if tid:
            base_query = base_query.where(ChatSession.tenant_id == tid)
        if status:
            base_query = base_query.where(ChatSession.status == status)
        if has_customer_info:
            base_query = base_query.where(ChatSession.customer_info.isnot(None))

        total_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
        total = total_result.scalar() or 0

        stmt = (
            base_query
            .order_by(ChatSession.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        sessions = result.scalars().all()

        items = []
        for sess in sessions:
            msg_result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == sess.id, ChatMessage.role == "user")
                .order_by(ChatMessage.id.asc())
                .limit(1)
            )
            first_msg = msg_result.scalar_one_or_none()

            msg_count_result = await db.execute(
                select(func.count(ChatMessage.id)).where(ChatMessage.session_id == sess.id)
            )
            msg_count = msg_count_result.scalar() or 0

            last_msg_result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == sess.id)
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
            last_msg = last_msg_result.scalar_one_or_none()

            item = {
                "session_key": sess.session_key,
                "status": sess.status,
                "user_name": sess.user_name,
                "first_message": first_msg.message[:100] if first_msg else "",
                "last_message": last_msg.message[:100] if last_msg else "",
                "message_count": msg_count,
                "created_at": sess.created_at.isoformat(),
                "last_message_at": last_msg.created_at.isoformat() if last_msg else None,
            }
            if hasattr(sess, "tenant_id"):
                item["tenant_id"] = sess.tenant_id
            items.append(item)

        return {"sessions": items, "total": total, "page": page, "page_size": page_size}

@router.get("/{session_key}/messages")
async def get_session_messages(session_key: str, admin=Depends(get_current_admin)):
    async with get_session_maker()() as db:
        session = await _get_session_with_tenant_check(db, session_key, admin)

        msg_result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.id.asc())
        )
        messages = msg_result.scalars().all()

        return {
            "session_key": session.session_key,
            "created_at": session.created_at.isoformat(),
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "message": m.message,
                    "retrieval_meta": m.retrieval_meta,
                    "created_at": m.created_at.isoformat(),
                }
                for m in messages
            ],
        }


class StatusUpdateRequest(BaseModel):
    status: str  # active, delayed, closed


@router.put("/{session_key}/status")
async def update_session_status(session_key: str, body: StatusUpdateRequest, admin=Depends(get_current_admin)):
    """세션 상태 변경 (active/delayed/closed)"""
    if body.status not in ("active", "delayed", "closed"):
        raise HTTPException(status_code=400, detail="유효하지 않은 상태입니다. (active, delayed, closed)")
    async with get_session_maker()() as db:
        session = await _get_session_with_tenant_check(db, session_key, admin)
        session.status = body.status
        await db.commit()
        return {"message": f"상태가 '{body.status}'로 변경되었습니다.", "session_key": session_key, "status": body.status}


@router.delete("/{session_key}")
async def delete_session(session_key: str, admin=Depends(get_current_admin)):
    """세션 삭제 (메시지 포함)"""
    async with get_session_maker()() as db:
        session = await _get_session_with_tenant_check(db, session_key, admin)
        await db.delete(session)
        await db.commit()
        return {"message": "세션이 삭제되었습니다.", "session_key": session_key}


from pydantic import Field

class AdminReplyRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)


@router.post("/{session_key}/reply")
async def admin_reply(session_key: str, body: AdminReplyRequest, admin=Depends(get_current_admin)):
    """관리자가 특정 세션에 직접 답변을 보냅니다."""
    async with get_session_maker()() as db:
        session = await _get_session_with_tenant_check(db, session_key, admin)

        msg = ChatMessage(
            session_id=session.id,
            role="assistant",
            message=body.message,
            retrieval_meta={"source": "admin", "admin_name": admin.name},
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)

        # WebSocket으로 사용자에게 실시간 전달
        from app.api.chat_ws import manager
        await manager.broadcast_to_session(session_key, {
            "type": "message",
            "role": "assistant",
            "message": body.message,
            "source": "admin",
            "session_key": session_key,
            "created_at": msg.created_at.isoformat() if msg.created_at else "",
        })

        return {
            "id": msg.id,
            "role": msg.role,
            "message": msg.message,
            "created_at": msg.created_at.isoformat(),
        }


class SetUserNameRequest(BaseModel):
    user_name: str


@router.put("/{session_key}/user-name")
async def set_user_name(session_key: str, body: SetUserNameRequest):
    """사용자가 자신의 이름을 설정 (인증 불필요)"""
    async with get_session_maker()() as db:
        result = await db.execute(select(ChatSession).where(ChatSession.session_key == session_key))
        session = result.scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        session.user_name = body.user_name.strip()[:100]
        await db.commit()
        return {"message": "이름이 설정되었습니다.", "user_name": session.user_name}


class CustomerInfoRequest(BaseModel):
    customer_info: dict


@router.put("/{session_key}/customer-info")
async def save_customer_info(session_key: str, body: CustomerInfoRequest, admin=Depends(get_current_admin)):
    """관리자가 고객 정보를 저장"""
    async with get_session_maker()() as db:
        session = await _get_session_with_tenant_check(db, session_key, admin)
        session.customer_info = body.customer_info
        await db.commit()
        return {"message": "고객 정보가 저장되었습니다."}


@router.get("/{session_key}/customer-info")
async def get_customer_info(session_key: str, admin=Depends(get_current_admin)):
    """고객 정보 + 대화 요약 조회"""
    async with get_session_maker()() as db:
        session = await _get_session_with_tenant_check(db, session_key, admin)

        return {
            "session_key": session.session_key,
            "user_name": session.user_name,
            "customer_info": session.customer_info,
            "status": session.status,
            "created_at": session.created_at.isoformat() if session.created_at else "",
        }


@router.post("/{session_key}/summarize")
async def summarize_session(session_key: str, admin=Depends(get_current_admin)):
    """대화 내용을 AI로 요약하여 고객 정보 자동 생성"""
    async with get_session_maker()() as db:
        session = await _get_session_with_tenant_check(db, session_key, admin)

        # 대화 내역 로드
        msgs_result = await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.id)
        )
        messages = msgs_result.scalars().all()
        if not messages:
            return {"summary": "대화 내역이 없습니다."}

        conversation = "\n".join(f"{'사용자' if m.role == 'user' else 'AI'}: {m.message}" for m in messages)

        # LLM으로 요약
        from app.services.llm_service import _get_client
        from app.core.config import get_settings
        settings = get_settings()
        client = _get_client()

        try:
            response = await client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": """대화 내용에서 고객 정보를 최대한 추출하여 JSON으로 정리하세요.

중요: 대화 속에 언급된 이름, 업체명, 전화번호, 주소, 이메일 등을 반드시 찾아서 해당 필드에 넣으세요.
예를 들어 "저희 회사는 ABC입니다", "010-1234-5678로 연락주세요", "강남구 테헤란로에 있습니다" 같은 내용이 있으면 반드시 추출하세요.

출력 형식:
{
    "workflow_status": "신규 접수/진행중/완료/보류 중 하나",
    "workflow_assignee": "담당자 추천 (예: 기술지원팀)",
    "company_name": "대화에서 언급된 업체명, 회사명, 이름 (반드시 추출)",
    "contact": "대화에서 언급된 전화번호, 이메일, 연락처 (반드시 추출)",
    "address": "대화에서 언급된 주소, 위치 (반드시 추출)",
    "extra_info": "인증, 환경, 사업 분야 등 추가 정보",
    "summary": "대화 요약 및 핵심 요구사항 (3-5문장)",
    "marketing_suggestion": "이 고객에게 제안할 마케팅 전략 (2-3문장)",
    "product_recommendation": "추천할 상품, 서비스, 또는 컨설팅 내용 (2-3문장)"
}

대화에서 정보를 찾을 수 없는 필드만 빈 문자열로 출력하세요. 조금이라도 관련 정보가 있으면 반드시 넣으세요."""},
                    {"role": "user", "content": conversation[:10000]},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=1024,
            )
            import json
            info = json.loads(response.choices[0].message.content)
            # 기존 정보가 있으면 메모/첨부/수동입력 유지
            if session.customer_info:
                for keep_key in ["memo", "attachments", "company_name", "contact", "address", "extra_info", "workflow_status", "workflow_assignee"]:
                    old_val = session.customer_info.get(keep_key)
                    new_val = info.get(keep_key)
                    if old_val and not new_val:
                        info[keep_key] = old_val
            if "memo" not in info:
                info["memo"] = session.customer_info.get("memo", "") if session.customer_info else ""
            if "attachments" not in info:
                info["attachments"] = session.customer_info.get("attachments", []) if session.customer_info else []

            # user_name이 설정되어 있고 company_name이 비어있으면 자동 채움
            if session.user_name and not info.get("company_name"):
                info["company_name"] = session.user_name

            # 저장
            session.customer_info = info
            await db.commit()

            return {"customer_info": info}
        except Exception as e:
            logger.error(f"Summarize failed: {e}")
            return {"error": "요약 생성에 실패했습니다."}
