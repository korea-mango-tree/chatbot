import jwt
import bcrypt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from app.core.config import get_settings
from app.core.db import get_session_maker

security = HTTPBearer()
settings = get_settings()

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_access_token(data: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    to_encode = {**data, "exp": expire}
    return jwt.encode(to_encode, settings.jwt_secret, algorithm="HS256")

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="토큰이 만료되었습니다.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")

async def get_current_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    payload = decode_token(credentials.credentials)
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")

    from app.models.admin import AdminUser
    async with get_session_maker()() as db:
        result = await db.execute(select(AdminUser).where(AdminUser.username == username))
        admin = result.scalar_one_or_none()
        if admin is None:
            raise HTTPException(status_code=401, detail="관리자를 찾을 수 없습니다.")
        return admin


def get_admin_tenant_id(admin) -> str | None:
    """Get tenant_id from admin. Returns None for superadmin (access all)."""
    if admin.role == "superadmin":
        return None
    return admin.tenant_id


async def get_super_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """슈퍼어드민만 접근 가능한 의존성"""
    admin = await get_current_admin(credentials)
    if admin.role != "superadmin":
        raise HTTPException(status_code=403, detail="슈퍼어드민 권한이 필요합니다.")
    return admin
