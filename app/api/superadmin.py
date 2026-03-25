"""슈퍼어드민 전용 API 라우터"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_, cast, String

from app.core.auth import get_super_admin, hash_password
from app.core.db import get_session_maker
from app.models.tenant import Tenant, UsageMeter, ApiLog
from app.models.admin import AdminUser
from app.models.document import Document, Chunk
from app.models.chat import ChatSession, ChatMessage

router = APIRouter(prefix="/superadmin", tags=["superadmin"])


# ── Pydantic schemas ──────────────────────────────────────────────

class TenantCreate(BaseModel):
    name: str
    slug: str
    plan: str = "free"
    max_documents: int = 100
    max_monthly_messages: int = 1000
    logo_url: Optional[str] = None
    primary_color: str = "#4a6cf7"
    welcome_message: Optional[str] = None


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    plan: Optional[str] = None
    status: Optional[str] = None
    max_documents: Optional[int] = None
    max_monthly_messages: Optional[int] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    welcome_message: Optional[str] = None
    openai_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    embedding_model: Optional[str] = None
    system_prompt: Optional[str] = None
    allowed_domains: Optional[list] = None


def _current_period() -> str:
    """Return current month as 'YYYY-MM' string."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ── GET /tenants ──────────────────────────────────────────────────

@router.get("/tenants")
async def list_tenants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(get_super_admin),
):
    period = _current_period()
    offset = (page - 1) * page_size

    async with get_session_maker()() as db:
        # total count
        total = (await db.execute(select(func.count(Tenant.id)))).scalar() or 0

        # tenants
        rows = (
            await db.execute(
                select(Tenant)
                .order_by(Tenant.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()

        # usage for current month
        tenant_ids = [t.id for t in rows]
        usage_map: dict[str, dict] = {}
        if tenant_ids:
            usages = (
                await db.execute(
                    select(UsageMeter).where(
                        and_(
                            UsageMeter.tenant_id.in_(tenant_ids),
                            UsageMeter.period == period,
                        )
                    )
                )
            ).scalars().all()
            for u in usages:
                usage_map[u.tenant_id] = {
                    "message_count": u.message_count,
                    "document_count": u.document_count,
                }

        tenants_out = []
        for t in rows:
            usage = usage_map.get(t.id, {"message_count": 0, "document_count": 0})
            tenants_out.append({
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "plan": t.plan,
                "status": t.status,
                "max_documents": t.max_documents,
                "max_monthly_messages": t.max_monthly_messages,
                "logo_url": t.logo_url,
                "primary_color": t.primary_color,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "usage": usage,
            })

    return {"tenants": tenants_out, "total": total, "page": page, "page_size": page_size}


# ── POST /tenants ─────────────────────────────────────────────────

@router.post("/tenants", status_code=201)
async def create_tenant(body: TenantCreate, _admin=Depends(get_super_admin)):
    async with get_session_maker()() as db:
        # slug 중복 확인
        exists = (
            await db.execute(select(Tenant).where(Tenant.slug == body.slug))
        ).scalar_one_or_none()
        if exists:
            raise HTTPException(status_code=400, detail="이미 존재하는 slug입니다.")

        tenant = Tenant(
            name=body.name,
            slug=body.slug,
            plan=body.plan,
            max_documents=body.max_documents,
            max_monthly_messages=body.max_monthly_messages,
            logo_url=body.logo_url,
            primary_color=body.primary_color,
            welcome_message=body.welcome_message,
        )
        db.add(tenant)
        await db.flush()

        # 테넌트 관리자 계정 자동 생성
        admin_username = f"{body.slug}_admin"
        admin_user = AdminUser(
            username=admin_username,
            password_hash=hash_password("admin1234"),
            name=f"{body.name} 관리자",
            role="admin",
        )
        db.add(admin_user)
        await db.commit()
        await db.refresh(tenant)

    return {
        "tenant": {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "plan": tenant.plan,
            "status": tenant.status,
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
        },
        "admin": {
            "username": admin_username,
            "default_password": "admin1234",
        },
    }


# ── GET /tenants/{tenant_id} ─────────────────────────────────────

@router.get("/tenants/{tenant_id}")
async def get_tenant(tenant_id: str, _admin=Depends(get_super_admin)):
    period = _current_period()

    async with get_session_maker()() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="테넌트를 찾을 수 없습니다.")

        usage = (
            await db.execute(
                select(UsageMeter).where(
                    and_(
                        UsageMeter.tenant_id == tenant_id,
                        UsageMeter.period == period,
                    )
                )
            )
        ).scalar_one_or_none()

    usage_data = {
        "message_count": usage.message_count if usage else 0,
        "document_count": usage.document_count if usage else 0,
        "embedding_tokens": usage.embedding_tokens if usage else 0,
        "llm_tokens": usage.llm_tokens if usage else 0,
    }

    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "plan": tenant.plan,
        "status": tenant.status,
        "logo_url": tenant.logo_url,
        "primary_color": tenant.primary_color,
        "welcome_message": tenant.welcome_message,
        "allowed_domains": tenant.allowed_domains,
        "openai_api_key": "***" if tenant.openai_api_key else None,
        "llm_model": tenant.llm_model,
        "embedding_model": tenant.embedding_model,
        "system_prompt": tenant.system_prompt,
        "max_documents": tenant.max_documents,
        "max_monthly_messages": tenant.max_monthly_messages,
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
        "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
        "usage": usage_data,
    }


# ── PUT /tenants/{tenant_id} ─────────────────────────────────────

@router.put("/tenants/{tenant_id}")
async def update_tenant(
    tenant_id: str, body: TenantUpdate, _admin=Depends(get_super_admin)
):
    async with get_session_maker()() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="테넌트를 찾을 수 없습니다.")

        update_data = body.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(tenant, key, value)

        await db.commit()
        await db.refresh(tenant)

    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "plan": tenant.plan,
        "status": tenant.status,
        "max_documents": tenant.max_documents,
        "max_monthly_messages": tenant.max_monthly_messages,
        "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
    }


# ── DELETE /tenants/{tenant_id} ───────────────────────────────────

@router.delete("/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str, _admin=Depends(get_super_admin)):
    async with get_session_maker()() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="테넌트를 찾을 수 없습니다.")

        tenant.status = "deleted"
        await db.commit()

    return {"detail": "테넌트가 삭제(비활성화)되었습니다."}


# ── GET /stats ────────────────────────────────────────────────────

@router.get("/stats")
async def platform_stats(_admin=Depends(get_super_admin)):
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    async with get_session_maker()() as db:
        total_tenants = (
            await db.execute(select(func.count(Tenant.id)))
        ).scalar() or 0

        active_tenants = (
            await db.execute(
                select(func.count(Tenant.id)).where(Tenant.status == "active")
            )
        ).scalar() or 0

        total_documents = (
            await db.execute(select(func.count(Document.id)))
        ).scalar() or 0

        total_chunks = (
            await db.execute(select(func.count(Chunk.id)))
        ).scalar() or 0

        total_sessions = (
            await db.execute(select(func.count(ChatSession.id)))
        ).scalar() or 0

        total_messages = (
            await db.execute(select(func.count(ChatMessage.id)))
        ).scalar() or 0

        today_messages = (
            await db.execute(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.created_at >= today_start
                )
            )
        ).scalar() or 0

    return {
        "total_tenants": total_tenants,
        "active_tenants": active_tenants,
        "total_documents": total_documents,
        "total_chunks": total_chunks,
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "today_messages": today_messages,
    }


# ── GET /logs ─────────────────────────────────────────────────────

@router.get("/logs")
async def api_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status_code: Optional[str] = Query(None, description="e.g. '5xx', '4xx', '2xx' or exact like '404'"),
    endpoint: Optional[str] = Query(None, description="endpoint contains filter"),
    date_from: Optional[str] = Query(None, description="ISO date string YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="ISO date string YYYY-MM-DD"),
    _admin=Depends(get_super_admin),
):
    offset = (page - 1) * page_size

    async with get_session_maker()() as db:
        q = select(ApiLog)
        count_q = select(func.count(ApiLog.id))

        filters = []

        # status code filter
        if status_code:
            if status_code.endswith("xx"):
                prefix = status_code[0]
                filters.append(
                    cast(ApiLog.status_code, String).like(f"{prefix}%")
                )
            else:
                filters.append(ApiLog.status_code == int(status_code))

        if endpoint:
            filters.append(ApiLog.endpoint.contains(endpoint))

        if date_from:
            filters.append(ApiLog.created_at >= datetime.fromisoformat(date_from))

        if date_to:
            # include the entire day
            dt_to = datetime.fromisoformat(date_to).replace(
                hour=23, minute=59, second=59
            )
            filters.append(ApiLog.created_at <= dt_to)

        if filters:
            q = q.where(and_(*filters))
            count_q = count_q.where(and_(*filters))

        total = (await db.execute(count_q)).scalar() or 0

        rows = (
            await db.execute(
                q.order_by(ApiLog.created_at.desc()).offset(offset).limit(page_size)
            )
        ).scalars().all()

    logs_out = [
        {
            "id": log.id,
            "tenant_id": log.tenant_id,
            "endpoint": log.endpoint,
            "method": log.method,
            "status_code": log.status_code,
            "response_time_ms": log.response_time_ms,
            "error_message": log.error_message,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in rows
    ]

    return {"logs": logs_out, "total": total, "page": page, "page_size": page_size}


# ── GET /usage ────────────────────────────────────────────────────

@router.get("/usage")
async def usage_overview(_admin=Depends(get_super_admin)):
    period = _current_period()

    async with get_session_maker()() as db:
        tenants = (
            await db.execute(
                select(Tenant).where(Tenant.status != "deleted").order_by(Tenant.name)
            )
        ).scalars().all()

        tenant_ids = [t.id for t in tenants]
        usage_map: dict[str, dict] = {}
        if tenant_ids:
            usages = (
                await db.execute(
                    select(UsageMeter).where(
                        and_(
                            UsageMeter.tenant_id.in_(tenant_ids),
                            UsageMeter.period == period,
                        )
                    )
                )
            ).scalars().all()
            for u in usages:
                usage_map[u.tenant_id] = {
                    "message_count": u.message_count,
                    "document_count": u.document_count,
                    "embedding_tokens": u.embedding_tokens,
                    "llm_tokens": u.llm_tokens,
                }

    result = []
    for t in tenants:
        usage = usage_map.get(t.id, {
            "message_count": 0,
            "document_count": 0,
            "embedding_tokens": 0,
            "llm_tokens": 0,
        })
        result.append({
            "tenant_id": t.id,
            "tenant_name": t.name,
            "tenant_slug": t.slug,
            "plan": t.plan,
            "max_documents": t.max_documents,
            "max_monthly_messages": t.max_monthly_messages,
            "period": period,
            **usage,
        })

    return {"period": period, "tenants": result}


# ── 테넌트 관리자 계정 관리 ──────────────────────────────────────

@router.get("/tenants/{tenant_id}/admins")
async def list_tenant_admins(tenant_id: str, _admin=Depends(get_super_admin)):
    """테넌트의 관리자 계정 목록"""
    async with get_session_maker()() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="테넌트를 찾을 수 없습니다.")

        result = await db.execute(
            select(AdminUser).where(AdminUser.tenant_id == tenant_id).order_by(AdminUser.created_at.asc())
        )
        admins = result.scalars().all()

    return {
        "tenant_id": tenant_id,
        "tenant_name": tenant.name,
        "admins": [
            {
                "id": a.id,
                "username": a.username,
                "name": a.name,
                "role": a.role,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in admins
        ],
    }


class TenantAdminCreate(BaseModel):
    username: str
    password: str
    name: str


@router.post("/tenants/{tenant_id}/admins", status_code=201)
async def create_tenant_admin(tenant_id: str, body: TenantAdminCreate, _admin=Depends(get_super_admin)):
    """테넌트에 관리자 계정 추가"""
    async with get_session_maker()() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="테넌트를 찾을 수 없습니다.")

        exists = (await db.execute(select(AdminUser).where(AdminUser.username == body.username))).scalar_one_or_none()
        if exists:
            raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")

        admin = AdminUser(
            username=body.username,
            password_hash=hash_password(body.password),
            name=body.name,
            role="admin",
            tenant_id=tenant_id,
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

    return {
        "id": admin.id,
        "username": admin.username,
        "name": admin.name,
        "role": admin.role,
        "tenant_id": tenant_id,
    }


@router.delete("/tenants/{tenant_id}/admins/{admin_id}")
async def delete_tenant_admin(tenant_id: str, admin_id: int, _admin=Depends(get_super_admin)):
    """테넌트의 관리자 계정 삭제"""
    async with get_session_maker()() as db:
        admin = (await db.execute(
            select(AdminUser).where(AdminUser.id == admin_id, AdminUser.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if not admin:
            raise HTTPException(status_code=404, detail="관리자를 찾을 수 없습니다.")

        await db.delete(admin)
        await db.commit()

    return {"detail": "관리자 계정이 삭제되었습니다."}


@router.put("/tenants/{tenant_id}/admins/{admin_id}/reset-password")
async def reset_admin_password(tenant_id: str, admin_id: int, _admin=Depends(get_super_admin)):
    """관리자 비밀번호 초기화 (admin1234)"""
    async with get_session_maker()() as db:
        admin = (await db.execute(
            select(AdminUser).where(AdminUser.id == admin_id, AdminUser.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if not admin:
            raise HTTPException(status_code=404, detail="관리자를 찾을 수 없습니다.")

        admin.password_hash = hash_password("admin1234")
        await db.commit()

    return {"detail": "비밀번호가 admin1234로 초기화되었습니다.", "username": admin.username}
