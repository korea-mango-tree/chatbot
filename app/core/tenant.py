"""테넌트 상태 확인 유틸리티"""

from fastapi import HTTPException
from sqlalchemy import select
from app.core.db import get_session_maker
from app.models.tenant import Tenant


async def get_tenant_by_id(tenant_id: str) -> Tenant | None:
    """tenant_id로 테넌트 조회"""
    if not tenant_id:
        return None
    async with get_session_maker()() as db:
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        return result.scalar_one_or_none()


async def check_tenant_active(tenant_id: str | None):
    """테넌트가 active 상태인지 확인. suspended/deleted면 예외 발생."""
    if not tenant_id:
        return  # tenant_id 없으면 통과 (아직 미연결 데이터)

    tenant = await get_tenant_by_id(tenant_id)
    if tenant is None:
        return  # 테넌트 없으면 통과

    if tenant.status == "suspended":
        raise HTTPException(
            status_code=403,
            detail="이 서비스는 일시정지 상태입니다. 관리자에게 문의하세요."
        )
    if tenant.status == "deleted":
        raise HTTPException(
            status_code=403,
            detail="이 서비스는 종료되었습니다."
        )


_default_tenant_id: str | None = None


async def get_default_tenant_id() -> str | None:
    """기본 테넌트(slug='default') ID를 캐싱하여 반환"""
    global _default_tenant_id
    if _default_tenant_id:
        return _default_tenant_id
    tenant = await get_tenant_by_slug("default")
    if tenant:
        _default_tenant_id = tenant.id
    return _default_tenant_id


async def get_tenant_by_slug(slug: str) -> Tenant | None:
    """slug로 테넌트 조회"""
    async with get_session_maker()() as db:
        result = await db.execute(select(Tenant).where(Tenant.slug == slug))
        return result.scalar_one_or_none()


def scoped_query(stmt, model, tenant_id: str | None):
    """Add tenant filter to SQLAlchemy statement. If tenant_id is None (superadmin), no filter."""
    if tenant_id is not None and hasattr(model, 'tenant_id'):
        return stmt.where(model.tenant_id == tenant_id)
    return stmt
