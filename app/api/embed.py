"""테넌트 브랜딩 정보 API (공개) + 도메인 화이트리스트 검증"""

import json
from fastapi import APIRouter, HTTPException, Query, Request

from app.core.tenant import get_tenant_by_slug

router = APIRouter(prefix="/embed", tags=["embed"])


def check_origin_allowed(allowed_domains, origin: str) -> bool:
    """Origin이 허용 도메인 목록에 있는지 확인. 비어있으면 전체 허용."""
    if not allowed_domains:
        return True

    domains = allowed_domains if isinstance(allowed_domains, list) else json.loads(allowed_domains or "[]")
    if not domains:
        return True

    if not origin:
        return True  # Origin 없는 요청 (서버 간 호출 등)은 허용

    origin_host = origin.replace("http://", "").replace("https://", "").split(":")[0]
    for d in domains:
        d = d.strip()
        if not d:
            continue
        if d in origin or origin_host == d:
            return True
    return False


@router.get("/config")
async def get_embed_config(request: Request, tenant: str = Query(..., description="테넌트 slug")):
    """테넌트 브랜딩 정보 반환 (인증 불필요)"""
    t = await get_tenant_by_slug(tenant)
    if t is None or t.status == "deleted":
        raise HTTPException(status_code=404, detail="존재하지 않는 서비스입니다.")

    origin = request.headers.get("origin", "")
    if not check_origin_allowed(t.allowed_domains, origin):
        raise HTTPException(status_code=403, detail="이 도메인에서는 접근이 허용되지 않습니다.")

    return {
        "tenant_id": t.id,
        "name": t.name,
        "slug": t.slug,
        "status": t.status,
        "logo_url": t.logo_url,
        "primary_color": t.primary_color,
        "welcome_message": t.welcome_message,
    }
