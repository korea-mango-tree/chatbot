import json
import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from app.core.db import get_session_maker
from app.core.auth import get_current_admin
from app.models.settings import SystemSetting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])

class SettingUpdate(BaseModel):
    key: str
    value: str

class SettingsBatchUpdate(BaseModel):
    settings: list[SettingUpdate]

@router.get("")
async def get_settings_all(admin=Depends(get_current_admin)):
    async with get_session_maker()() as db:
        result = await db.execute(select(SystemSetting))
        settings = result.scalars().all()
        return {
            "settings": {s.key: s.value for s in settings}
        }

@router.put("")
async def update_settings(body: SettingsBatchUpdate, admin=Depends(get_current_admin)):
    async with get_session_maker()() as db:
        for item in body.settings:
            existing = await db.get(SystemSetting, item.key)
            if existing:
                existing.value = item.value
            else:
                db.add(SystemSetting(key=item.key, value=item.value))
        await db.commit()
        return {"message": "설정이 저장되었습니다."}
