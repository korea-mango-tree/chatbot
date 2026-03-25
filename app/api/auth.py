import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from app.core.db import get_session_maker
from app.core.auth import hash_password, verify_password, create_access_token, get_current_admin
from app.models.admin import AdminUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    admin: dict

class AdminInfo(BaseModel):
    id: int
    username: str
    name: str
    role: str
    tenant_id: str | None = None
    tenant_slug: str | None = None

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    async with get_session_maker()() as db:
        result = await db.execute(select(AdminUser).where(AdminUser.username == body.username))
        admin = result.scalar_one_or_none()
        if admin is None or not verify_password(body.password, admin.password_hash):
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")

        token = create_access_token({"sub": admin.username, "tenant_id": admin.tenant_id})
        return LoginResponse(
            access_token=token,
            admin={"id": admin.id, "username": admin.username, "name": admin.name, "role": admin.role, "tenant_id": admin.tenant_id}
        )

@router.get("/me", response_model=AdminInfo)
async def get_me(admin=Depends(get_current_admin)):
    tenant_slug = None
    if admin.tenant_id:
        async with get_session_maker()() as db:
            from app.models.tenant import Tenant
            t = await db.get(Tenant, admin.tenant_id)
            if t:
                tenant_slug = t.slug
    return AdminInfo(id=admin.id, username=admin.username, name=admin.name, role=admin.role, tenant_id=admin.tenant_id, tenant_slug=tenant_slug)

@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, admin=Depends(get_current_admin)):
    async with get_session_maker()() as db:
        db_admin = await db.get(AdminUser, admin.id)
        if not verify_password(body.current_password, db_admin.password_hash):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
        db_admin.password_hash = hash_password(body.new_password)
        await db.commit()
        return {"message": "비밀번호가 변경되었습니다."}
