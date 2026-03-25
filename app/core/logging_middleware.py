"""API 요청/응답 로깅 미들웨어"""

import asyncio
import logging
import time
import traceback

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.db import get_session_maker
from app.models.tenant import ApiLog

logger = logging.getLogger(__name__)

# Paths to skip logging
_SKIP_PREFIXES = ("/static", "/ws", "/favicon.ico")


class ApiLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Skip static files, websocket, favicon
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        start = time.time()
        error_message = None
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            raise
        finally:
            elapsed_ms = int((time.time() - start) * 1000)
            # Fire-and-forget: don't block the response
            asyncio.create_task(
                _save_log(
                    endpoint=path,
                    method=request.method,
                    status_code=status_code,
                    response_time_ms=elapsed_ms,
                    error_message=error_message,
                )
            )


async def _save_log(
    endpoint: str,
    method: str,
    status_code: int,
    response_time_ms: int,
    error_message: str | None,
):
    """Background task to persist an API log entry."""
    try:
        async with get_session_maker()() as db:
            log = ApiLog(
                endpoint=endpoint,
                method=method,
                status_code=status_code,
                response_time_ms=response_time_ms,
                error_message=error_message,
            )
            db.add(log)
            await db.commit()
    except Exception:
        logger.warning("API 로그 저장 실패: %s", traceback.format_exc())
