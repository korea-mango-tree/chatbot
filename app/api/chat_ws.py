"""WebSocket endpoints for real-time chat between users and admins."""

import json
import logging
import traceback
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_session_maker
from app.core.tenant import check_tenant_active, get_default_tenant_id, get_tenant_by_slug

settings = get_settings()
from app.models.chat import ChatSession, ChatMessage
from app.graphs.chat_graph import chat_graph, ChatState

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_chat_settings(tenant_id: str | None = None) -> dict:
    """채팅 운영 설정 조회"""
    from app.models.settings import SystemSetting
    defaults = {
        "chat_enabled": "true",
        "chat_days": '["mon","tue","wed","thu","fri","sat","sun"]',
        "chat_start_time": "00:00",
        "chat_end_time": "23:59",
        "ai_auto_reply": "true",
    }
    async with get_session_maker()() as db:
        result = await db.execute(select(SystemSetting).where(SystemSetting.key.in_(defaults.keys())))
        for s in result.scalars().all():
            defaults[s.key] = s.value
    return defaults


def check_chat_availability(chat_settings: dict) -> str | None:
    """채팅 가능 여부 확인. 불가하면 메시지 반환, 가능하면 None"""
    import datetime

    # 채팅 비활성화
    if chat_settings.get("chat_enabled") == "false":
        return "현재 채팅 서비스가 비활성화되어 있습니다."

    now = datetime.datetime.now()

    # 요일 체크
    day_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
    day_names = {"mon": "월", "tue": "화", "wed": "수", "thu": "목", "fri": "금", "sat": "토", "sun": "일"}
    today = day_map[now.weekday()]
    try:
        allowed_days = json.loads(chat_settings.get("chat_days", "[]"))
    except Exception:
        allowed_days = list(day_map.values())

    if today not in allowed_days:
        days_str = ", ".join(day_names.get(d, d) for d in allowed_days)
        return f"현재 채팅 가능한 요일이 아닙니다. 운영 요일: {days_str}"

    # 시간 체크
    start = chat_settings.get("chat_start_time", "00:00")
    end = chat_settings.get("chat_end_time", "23:59")
    now_time = now.strftime("%H:%M")
    if not (start <= now_time <= end):
        return f"현재 상담 시간이 아닙니다. 상담 시간: {start}~{end}"

    return None


class ConnectionManager:
    """세션별 WebSocket 연결 관리"""

    def __init__(self):
        # session_key -> list of (ws, role) tuples
        self.connections: dict[str, list[tuple[WebSocket, str]]] = {}
        # admin connections watching the session list
        self.admin_watchers: list[WebSocket] = []

    async def connect(self, session_key: str, ws: WebSocket, role: str):
        await ws.accept()
        if session_key not in self.connections:
            self.connections[session_key] = []
        self.connections[session_key].append((ws, role))
        logger.info(f"[WS] {role} connected to session '{session_key}' (total: {len(self.connections[session_key])})")

    def disconnect(self, session_key: str, ws: WebSocket):
        if session_key in self.connections:
            self.connections[session_key] = [
                (w, r) for w, r in self.connections[session_key] if w != ws
            ]
            remaining = len(self.connections.get(session_key, []))
            logger.info(f"[WS] disconnected from session '{session_key}' (remaining: {remaining})")
            if not self.connections[session_key]:
                del self.connections[session_key]

    async def connect_admin_watcher(self, ws: WebSocket):
        await ws.accept()
        self.admin_watchers.append(ws)

    def disconnect_admin_watcher(self, ws: WebSocket):
        self.admin_watchers = [w for w in self.admin_watchers if w != ws]

    async def broadcast_to_session(self, session_key: str, message: dict, exclude_ws: WebSocket = None):
        """세션의 모든 연결에 메시지 전송"""
        if session_key not in self.connections:
            logger.warning(f"[WS] broadcast: no connections for session '{session_key}'")
            return
        conns = self.connections[session_key]
        sent_count = 0
        dead = []
        for ws, role in conns:
            if ws == exclude_ws:
                continue
            try:
                await ws.send_json(message)
                sent_count += 1
                logger.info(f"[WS] broadcast to {role} in '{session_key}': {message.get('role','?')} msg")
            except Exception as e:
                logger.error(f"[WS] broadcast failed to {role}: {e}")
                dead.append(ws)
        if sent_count == 0:
            logger.warning(f"[WS] broadcast: 0 recipients for session '{session_key}' (total conns: {len(conns)}, excluded: 1)")
        for ws in dead:
            self.disconnect(session_key, ws)

    async def notify_admin_watchers(self, event: dict):
        """관리자 목록 감시자에게 새 이벤트 알림"""
        dead = []
        for ws in self.admin_watchers:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_admin_watcher(ws)


manager = ConnectionManager()


@router.websocket("/ws/chat/{session_key}")
async def user_chat_ws(ws: WebSocket, session_key: str, tenant: str | None = None):
    """사용자 WebSocket - 메시지 전송 및 AI/관리자 답변 수신"""
    # tenant slug로 tenant_id 결정
    resolved_tenant_id = None
    if tenant:
        t = await get_tenant_by_slug(tenant)
        if t and t.status == "active":
            resolved_tenant_id = t.id
            # 도메인 화이트리스트 검증
            from app.api.embed import check_origin_allowed
            origin = ws.headers.get("origin", "")
            if not check_origin_allowed(t.allowed_domains, origin):
                await ws.close(code=4003, reason="도메인이 허용되지 않습니다.")
                return
        else:
            await ws.close(code=4003, reason="존재하지 않거나 비활성 테넌트입니다.")
            return
    else:
        resolved_tenant_id = await get_default_tenant_id()

    await manager.connect(session_key, ws, "user")
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)

            # 이름 설정 메시지 처리
            if msg.get("type") == "set_name":
                name = msg.get("name", "").strip()[:100]
                if name:
                    async with get_session_maker()() as db:
                        result = await db.execute(select(ChatSession).where(ChatSession.session_key == session_key))
                        sess = result.scalar_one_or_none()
                        if sess is None:
                            # 세션이 아직 없으면 생성
                            sess = ChatSession(session_key=session_key, tenant_id=resolved_tenant_id, user_name=name)
                            db.add(sess)
                        else:
                            sess.user_name = name
                        await db.commit()
                    await ws.send_json({"type": "name_set", "name": name})
                continue

            user_text = msg.get("message", "").strip()
            if not user_text:
                continue

            async with get_session_maker()() as db:
                # 세션 조회/생성
                result = await db.execute(
                    select(ChatSession).where(ChatSession.session_key == session_key)
                )
                session = result.scalar_one_or_none()
                if session is None:
                    session = ChatSession(session_key=session_key, tenant_id=resolved_tenant_id)
                    db.add(session)
                    await db.flush()

                # 테넌트 상태 확인 (suspended/deleted면 차단)
                try:
                    await check_tenant_active(session.tenant_id)
                except Exception as e:
                    await ws.send_json({"type": "error", "message": str(e.detail) if hasattr(e, 'detail') else str(e)})
                    continue

                # 채팅 운영 설정 확인
                chat_cfg = await get_chat_settings(session.tenant_id)
                unavail_msg = check_chat_availability(chat_cfg)
                if unavail_msg:
                    await ws.send_json({"type": "message", "role": "assistant", "message": unavail_msg, "sources": []})
                    continue

                # 사용자 메시지 저장
                user_msg = ChatMessage(session_id=session.id, role="user", message=user_text)
                db.add(user_msg)
                await db.flush()

                # 사용자 메시지를 세션 전체에 브로드캐스트 (관리자에게)
                user_event = {
                    "type": "message",
                    "role": "user",
                    "message": user_text,
                    "session_key": session_key,
                    "created_at": user_msg.created_at.isoformat() if user_msg.created_at else "",
                }
                await manager.broadcast_to_session(session_key, user_event, exclude_ws=ws)
                await manager.notify_admin_watchers({"type": "new_message", "session_key": session_key, "preview": user_text[:80]})

                # AI 자동 답변 체크: 세션별 설정 > 글로벌 설정
                session_ai = None
                if session.customer_info and isinstance(session.customer_info, dict):
                    session_ai = session.customer_info.get("ai_auto_reply")
                # 세션별 설정이 있으면 우선, 없으면 글로벌
                ai_enabled = session_ai if session_ai in ("true", "false") else chat_cfg.get("ai_auto_reply", "true")

                if ai_enabled == "false":
                    await db.commit()
                    # 자동 응답 없이 관리자에게만 알림
                    await manager.notify_admin_watchers({
                        "type": "unanswered_question",
                        "session_key": session_key,
                        "question": user_text,
                        "message": f"[AI OFF] \"{user_text[:50]}\" — 관리자 답변 필요",
                    })
                    continue

                # Load last 10 messages (5 pairs) for context — excluding the just-saved message
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

                # AI 답변 생성
                try:
                    initial_state = ChatState(
                        question=user_text,
                        chat_history=chat_history,
                        db=db,
                        tenant_id=session.tenant_id,
                    )
                    final_state = await chat_graph.ainvoke(initial_state)
                    answer = final_state["answer"]
                    sources = final_state["sources"]
                except Exception:
                    logger.error(traceback.format_exc())
                    answer = "죄송합니다. 답변 생성 중 오류가 발생했습니다."
                    sources = []

                # AI 답변 저장
                ai_msg = ChatMessage(
                    session_id=session.id,
                    role="assistant",
                    message=answer,
                    retrieval_meta={"sources": sources},
                )
                db.add(ai_msg)
                await db.commit()

                # AI 답변을 세션 전체에 브로드캐스트
                ai_event = {
                    "type": "message",
                    "role": "assistant",
                    "message": answer,
                    "sources": sources,
                    "session_key": session_key,
                    "created_at": ai_msg.created_at.isoformat() if ai_msg.created_at else "",
                }
                await manager.broadcast_to_session(session_key, ai_event)

                # 미답변 감지 → 관리자에게 알림 (answerable 기반)
                from app.graphs.chat_graph import _is_greeting
                answerable = final_state.get("answerable", True)
                if not answerable and not _is_greeting(user_text):
                    await manager.notify_admin_watchers({
                        "type": "unanswered_question",
                        "session_key": session_key,
                        "question": user_text,
                        "confidence": final_state.get("confidence_score", 0.0),
                        "message": f"[미답변] \"{user_text[:50]}\"",
                    })

    except WebSocketDisconnect:
        manager.disconnect(session_key, ws)
    except Exception:
        logger.error(traceback.format_exc())
        manager.disconnect(session_key, ws)


@router.websocket("/ws/admin/{session_key}")
async def admin_chat_ws(ws: WebSocket, session_key: str):
    """관리자 WebSocket - 특정 세션의 실시간 메시지 수신 및 답변 전송"""
    await manager.connect(session_key, ws, "admin")
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            admin_text = msg.get("message", "").strip()
            if not admin_text:
                continue

            async with get_session_maker()() as db:
                result = await db.execute(
                    select(ChatSession).where(ChatSession.session_key == session_key)
                )
                session = result.scalar_one_or_none()
                if session is None:
                    await ws.send_json({"type": "error", "message": "세션을 찾을 수 없습니다."})
                    continue

                admin_msg = ChatMessage(
                    session_id=session.id,
                    role="assistant",
                    message=admin_text,
                    retrieval_meta={"source": "admin"},
                )
                db.add(admin_msg)
                await db.commit()
                await db.refresh(admin_msg)

                event = {
                    "type": "message",
                    "role": "assistant",
                    "message": admin_text,
                    "source": "admin",
                    "session_key": session_key,
                    "created_at": admin_msg.created_at.isoformat() if admin_msg.created_at else "",
                }
                await manager.broadcast_to_session(session_key, event, exclude_ws=ws)

    except WebSocketDisconnect:
        manager.disconnect(session_key, ws)
    except Exception:
        logger.error(traceback.format_exc())
        manager.disconnect(session_key, ws)


@router.websocket("/ws/admin-watch")
async def admin_watch_ws(ws: WebSocket):
    """관리자 채팅 목록 실시간 감시 - 새 메시지 알림 수신"""
    await manager.connect_admin_watcher(ws)
    try:
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        manager.disconnect_admin_watcher(ws)
    except Exception:
        manager.disconnect_admin_watcher(ws)
