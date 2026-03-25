import logging
import traceback

from fastapi import APIRouter, UploadFile, File, Form, Depends
from pydantic import BaseModel

from app.core.db import get_session_maker
from app.core.auth import get_current_admin, get_admin_tenant_id
from app.services.ingest_service import ingest_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestTextRequest(BaseModel):
    title: str
    content: str
    source_type: str = "faq"
    metadata: dict | None = None


class QualityIssueResponse(BaseModel):
    level: str
    message: str


class IngestTextResponse(BaseModel):
    document_id: int
    title: str
    chunk_count: int
    quality_score: int | None = None
    quality_issues: list[QualityIssueResponse] = []
    quality_suggestions: list[str] = []
    duplicate_warning: list[dict] | None = None
    was_converted: bool = False
    detected_format: str = "text"
    auto_metadata: dict = {}


@router.post("/text", response_model=IngestTextResponse)
async def ingest_text_endpoint(body: IngestTextRequest, admin=Depends(get_current_admin)):
    tid = get_admin_tenant_id(admin)
    async with get_session_maker()() as db:
        try:
            doc = await ingest_text(
                db=db,
                title=body.title,
                content=body.content,
                source_type=body.source_type,
                metadata=body.metadata,
                tenant_id=tid,
            )
            return _build_response(doc)
        except Exception:
            logger.error(traceback.format_exc())
            raise


class IngestFileResultItem(BaseModel):
    document_id: int
    title: str
    chunk_count: int
    success: bool = True
    error: str | None = None
    quality_score: int | None = None
    quality_issues: list[QualityIssueResponse] = []
    quality_suggestions: list[str] = []
    was_converted: bool = False
    detected_format: str = "text"


class IngestFilesResponse(BaseModel):
    results: list[IngestFileResultItem]
    total: int
    success_count: int


def _decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8", "euc-kr", "cp949", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _build_response(doc) -> IngestTextResponse:
    """IngestResult → API 응답 변환"""
    quality_issues = []
    quality_suggestions = []
    quality_score = None
    duplicate_warning = None

    if doc.quality_report:
        quality_score = doc.quality_report.score
        quality_issues = [
            QualityIssueResponse(level=i.level, message=i.message)
            for i in doc.quality_report.issues
        ]
        quality_suggestions = doc.quality_report.suggestions
        if doc.quality_report.duplicate_docs:
            duplicate_warning = doc.quality_report.duplicate_docs

    return IngestTextResponse(
        document_id=doc.id,
        title=doc.title,
        chunk_count=doc.chunk_count,
        quality_score=quality_score,
        quality_issues=quality_issues,
        quality_suggestions=quality_suggestions,
        duplicate_warning=duplicate_warning,
        was_converted=doc.was_converted,
        detected_format=doc.detected_format,
        auto_metadata=doc.auto_metadata,
    )


@router.post("/file", response_model=IngestFilesResponse)
async def ingest_file_endpoint(
    files: list[UploadFile] = File(...),
    source_type: str = Form("file"),
    admin=Depends(get_current_admin),
):
    from app.services.file_parser import is_binary_file, extract_text

    tid = get_admin_tenant_id(admin)
    results = []
    async with get_session_maker()() as db:
        for file in files:
            try:
                content_bytes = await file.read()
                title = file.filename or "uploaded_file"

                # 바이너리 파일(PDF, DOCX, PPTX, XLSX)은 텍스트 추출
                if is_binary_file(title):
                    raw_content = extract_text(title, content_bytes)
                else:
                    raw_content = _decode_bytes(content_bytes)

                # 전처리는 ingest_text 내부에서 자동 수행
                doc = await ingest_text(
                    db=db,
                    title=title,
                    content=raw_content,
                    source_type=source_type,
                    metadata={"filename": file.filename, "content_type": file.content_type},
                    tenant_id=tid,
                )

                quality_issues = []
                quality_suggestions = []
                quality_score = None
                if doc.quality_report:
                    quality_score = doc.quality_report.score
                    quality_issues = [
                        QualityIssueResponse(level=i.level, message=i.message)
                        for i in doc.quality_report.issues
                    ]
                    quality_suggestions = doc.quality_report.suggestions

                results.append(IngestFileResultItem(
                    document_id=doc.id,
                    title=doc.title,
                    chunk_count=doc.chunk_count,
                    quality_score=quality_score,
                    quality_issues=quality_issues,
                    quality_suggestions=quality_suggestions,
                    was_converted=doc.was_converted,
                    detected_format=doc.detected_format,
                ))
            except Exception:
                logger.error(traceback.format_exc())
                results.append(IngestFileResultItem(
                    document_id=-1, title=file.filename or "unknown",
                    chunk_count=0, success=False, error=traceback.format_exc(),
                ))

    return IngestFilesResponse(
        results=results,
        total=len(results),
        success_count=sum(1 for r in results if r.success),
    )
