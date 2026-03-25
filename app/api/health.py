import os
import signal
import sys

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok"}


@router.post("/restart")
async def restart_server():
    """서버 재시작 (--reload 모드에서 동작)"""
    # reload 모드: 파일 변경 감지로 재시작 트리거
    # 자기 자신(main.py)을 touch해서 watchfiles가 감지하도록 함
    from pathlib import Path
    main_file = Path(__file__).resolve().parent.parent / "main.py"
    main_file.touch()
    return {"message": "서버 재시작 중..."}
