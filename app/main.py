import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.core.db import init_db, get_session_maker
from app.core.auth import hash_password
from app.api import health, ingest, chat, documents, groups, auth, stats, sessions, settings_api, search, faq, chat_ws, superadmin, embed
from app.core.logging_middleware import ApiLoggingMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def create_default_admin():
    from app.models.admin import AdminUser
    from app.core.config import get_settings
    settings = get_settings()

    async with get_session_maker()() as db:
        result = await db.execute(select(AdminUser).where(AdminUser.username == settings.admin_default_username))
        if result.scalar_one_or_none() is None:
            admin = AdminUser(
                username=settings.admin_default_username,
                password_hash=hash_password(settings.admin_default_password),
                name="관리자",
                role="admin",
            )
            db.add(admin)
            await db.commit()
            logger.info(f"기본 관리자 계정 생성: {settings.admin_default_username}")

        # 슈퍼어드민 계정 생성
        result = await db.execute(select(AdminUser).where(AdminUser.username == "superadmin"))
        if result.scalar_one_or_none() is None:
            superadmin = AdminUser(
                username="superadmin",
                password_hash=hash_password("super1234"),
                name="슈퍼관리자",
                role="superadmin",
            )
            db.add(superadmin)
            await db.commit()
            logger.info("슈퍼어드민 계정 생성: superadmin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await create_default_admin()
    yield


app = FastAPI(
    title="RAG Chatbot API",
    description="로컬 테스트용 RAG 기반 AI 챗봇",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS - 외부 도메인에서 위젯 로드 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 라우터 (all under /api)
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(groups.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(settings_api.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(faq.router, prefix="/api")
app.include_router(superadmin.router, prefix="/api")
app.include_router(embed.router, prefix="/api")
app.include_router(chat_ws.router)

# API logging middleware
app.add_middleware(ApiLoggingMiddleware)


# Page routes
@app.get("/")
async def user_page():
    return FileResponse(STATIC_DIR / "user" / "index.html")


@app.get("/chat/{tenant_slug}")
async def tenant_chat_page(tenant_slug: str):
    """테넌트별 사용자 채팅 페이지"""
    return FileResponse(STATIC_DIR / "user" / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/superadmin")
async def superadmin_page():
    return FileResponse(STATIC_DIR / "superadmin" / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/superadmin/login")
async def superadmin_login_page():
    return FileResponse(STATIC_DIR / "superadmin" / "login.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/admin")
async def admin_page():
    return FileResponse(STATIC_DIR / "admin" / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/admin/login")
async def admin_login_page():
    return FileResponse(STATIC_DIR / "admin" / "login.html")


@app.get("/admin/login.html")
async def admin_login_page_html():
    return FileResponse(STATIC_DIR / "admin" / "login.html")


@app.get("/admin/{path:path}")
async def admin_catch_all(path: str):  # noqa: ARG001
    """Admin SPA catch-all - hash routing을 위해 항상 index.html 반환"""
    return FileResponse(STATIC_DIR / "admin" / "index.html")


@app.get("/embed/chat")
async def embed_chat_page():
    return FileResponse(STATIC_DIR / "embed" / "chat.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/embed/widget.js")
async def embed_widget_js():
    return FileResponse(STATIC_DIR / "embed" / "widget.js", media_type="application/javascript", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/embed/demo")
async def embed_demo_page(tenant: str = "default"):
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>임베드 위젯 데모</title>
<style>body{{font-family:sans-serif;padding:40px;background:#f5f5f5;}}h1{{color:#333;}}p{{color:#666;margin:20px 0;}}.info{{background:#fff;padding:20px;border-radius:8px;border:1px solid #ddd;margin:20px 0;}}</style>
</head><body>
<h1>고객 웹사이트 (데모)</h1>
<p>이 페이지는 고객의 실제 웹사이트를 시뮬레이션합니다.</p>
<div class="info">
<p>우하단의 <strong>채팅 버블</strong>을 클릭하면 AI 챗봇이 열립니다.</p>
<p>테넌트: <code>{tenant}</code></p>
</div>
<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>
<script src="/embed/widget.js" data-tenant="{tenant}"></script>
</body></html>"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
