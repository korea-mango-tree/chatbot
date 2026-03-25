"""데이터 품질 자동 전처리 파이프라인

① 형식 감지 + 자연어 변환
② 자동 메타데이터 생성 (제목/키워드/요약/카테고리)
③ 품질 검증 (길이/중복/의미 체크)
"""

import json
import logging
import re
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.services.llm_service import _get_client
from app.services.embedding_service import create_embedding

logger = logging.getLogger(__name__)
settings = get_settings()


# ─── 데이터 클래스 ───

@dataclass
class PreprocessResult:
    content: str
    original_content: str
    was_converted: bool = False
    detected_format: str = "text"  # text, sql, json, csv


@dataclass
class DocumentMetadata:
    auto_title: str = ""
    keywords: list[str] = field(default_factory=list)
    summary: str = ""
    category: str = ""


@dataclass
class QualityIssue:
    level: str = "warning"  # warning, error, info
    message: str = ""


@dataclass
class QualityReport:
    score: int = 100  # 0~100
    issues: list[QualityIssue] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    duplicate_docs: list[dict] = field(default_factory=list)


# ─── ① 형식 감지 + 변환 ───

def detect_format(content: str, filename: str = "") -> str:
    """파일 내용과 확장자로 형식 감지"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "sql" or _looks_like_sql(content):
        return "sql"
    if ext == "json" or _looks_like_json(content):
        return "json"
    if ext == "csv" or _looks_like_csv(content):
        return "csv"
    return "text"


def _looks_like_sql(content: str) -> bool:
    sql_keywords = ["INSERT INTO", "CREATE TABLE", "SELECT ", "ALTER TABLE", "DROP TABLE"]
    upper = content[:2000].upper()
    return any(kw in upper for kw in sql_keywords)


def _looks_like_json(content: str) -> bool:
    stripped = content.strip()
    return (stripped.startswith("{") and stripped.endswith("}")) or \
           (stripped.startswith("[") and stripped.endswith("]"))


def _looks_like_csv(content: str) -> bool:
    lines = content.strip().split("\n")[:5]
    if len(lines) < 2:
        return False
    comma_counts = [line.count(",") for line in lines]
    return all(c > 0 for c in comma_counts) and len(set(comma_counts)) <= 2


def strip_html_tags(text: str) -> str:
    """HTML 태그 제거 + 엔티티 디코딩"""
    import html
    # HTML 태그 제거
    cleaned = re.sub(r'<[^>]+>', ' ', text)
    # HTML 엔티티 디코딩 (&amp; → &, &lt; → < 등)
    cleaned = html.unescape(cleaned)
    # 연속 공백 정리
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    # 연속 줄바꿈 정리
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def _contains_html(content: str) -> bool:
    """HTML 태그가 포함되어 있는지 확인"""
    return bool(re.search(r'<(p|div|span|strong|br|h[1-6]|ul|ol|li|a|img|table|tr|td|th|iframe|blockquote)\b', content, re.IGNORECASE))


async def preprocess_content(content: str, source_type: str, filename: str = "") -> PreprocessResult:
    """업로드 데이터를 검색에 최적화된 형태로 변환"""
    # HTML 태그가 포함되어 있으면 자동 제거
    if _contains_html(content):
        content = strip_html_tags(content)

    detected = detect_format(content, filename)

    if detected == "sql":
        from app.services.sql_parser_service import sql_to_description
        converted = await sql_to_description(content, filename)
        converted = f"[원본 파일: {filename}]\n\n{converted}"
        return PreprocessResult(
            content=converted, original_content=content,
            was_converted=True, detected_format="sql"
        )

    if detected == "json":
        converted = await _json_to_description(content, filename)
        return PreprocessResult(
            content=converted, original_content=content,
            was_converted=True, detected_format="json"
        )

    if detected == "csv":
        converted = await _csv_to_description(content, filename)
        return PreprocessResult(
            content=converted, original_content=content,
            was_converted=True, detected_format="csv"
        )

    # 텍스트는 그대로
    return PreprocessResult(
        content=content, original_content=content,
        was_converted=False, detected_format="text"
    )


async def _json_to_description(json_content: str, filename: str) -> str:
    """JSON 데이터를 자연어 설명으로 변환"""
    client = _get_client()

    # JSON이 너무 길면 잘라서 처리
    truncated = json_content[:12000]

    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": """당신은 JSON 데이터를 분석해서 자연어로 설명하는 전문가입니다.

주어진 JSON 내용을 분석하여 다음 형식으로 작성하세요:

## 전체 요약
- 이 데이터의 목적과 구조를 2~3문장으로 요약

## 데이터 상세
- 모든 항목을 빠짐없이 자연어로 설명
- 절대 생략하지 말 것

한국어로 상세하게 작성하세요. JSON 코드는 포함하지 마세요."""},
            {"role": "user", "content": f"파일명: {filename}\n\nJSON 내용:\n{truncated}"},
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    result = response.choices[0].message.content
    return f"[원본 파일: {filename}]\n\n{result}"


async def _csv_to_description(csv_content: str, filename: str) -> str:
    """CSV 데이터를 자연어 설명으로 변환"""
    client = _get_client()

    truncated = csv_content[:12000]

    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": """당신은 CSV 데이터를 분석해서 자연어로 설명하는 전문가입니다.

주어진 CSV 내용을 분석하여 다음 형식으로 작성하세요:

## 전체 요약
- 이 데이터의 목적과 구조를 2~3문장으로 요약

## 컬럼 설명
- 각 컬럼의 의미

## 데이터 상세
- 모든 행을 빠짐없이 설명
- 절대 생략하지 말 것

한국어로 상세하게 작성하세요."""},
            {"role": "user", "content": f"파일명: {filename}\n\nCSV 내용:\n{truncated}"},
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    result = response.choices[0].message.content
    return f"[원본 파일: {filename}]\n\n{result}"


# ─── ② 자동 메타데이터 생성 ───

def looks_like_filename(title: str) -> bool:
    """제목이 파일명처럼 보이는지 확인"""
    ext_pattern = r'\.\w{1,5}$'
    if re.search(ext_pattern, title):
        return True
    # 공백 없이 확장자만 있는 경우
    if "." in title and " " not in title.strip():
        return True
    return False


async def generate_metadata(content: str, filename: str = "") -> DocumentMetadata:
    """LLM으로 문서 메타데이터 자동 생성"""
    client = _get_client()

    truncated = content[:3000]

    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": """문서 내용을 분석하여 메타데이터를 JSON으로 생성하세요.

출력 형식:
{
    "auto_title": "검색에 유리한 한국어 제목 (20자 이내)",
    "keywords": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"],
    "summary": "1-2문장 요약",
    "category": "카테고리 (상품/결제/배송/계정/기능/정책/기타 중 하나)"
}"""},
                {"role": "user", "content": f"파일명: {filename}\n\n내용:\n{truncated}"},
            ],
            temperature=0.0,
            max_tokens=512,
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content)
        return DocumentMetadata(
            auto_title=data.get("auto_title", ""),
            keywords=data.get("keywords", []),
            summary=data.get("summary", ""),
            category=data.get("category", "기타"),
        )
    except Exception as e:
        logger.warning(f"메타데이터 생성 실패: {e}")
        return DocumentMetadata()


# ─── ③ 품질 검증 ───

async def validate_quality(
    content: str,
    tenant_id: str | None = None,
    db=None,
) -> QualityReport:
    """업로드 데이터의 검색 적합도 평가"""
    report = QualityReport()

    # 길이 체크
    if len(content.strip()) < 50:
        report.score -= 40
        report.issues.append(QualityIssue("error", "내용이 너무 짧습니다 (50자 미만). 검색에 불리합니다."))
        report.suggestions.append("더 상세한 내용을 추가해 주세요.")

    elif len(content) > 50000:
        report.score -= 10
        report.issues.append(QualityIssue("warning", "내용이 매우 깁니다 (50,000자 초과). 문서를 분리하는 것을 권장합니다."))
        report.suggestions.append("주제별로 문서를 나누면 검색 정확도가 높아집니다.")

    # 의미 없는 내용 체크
    stripped = re.sub(r'\s+', '', content)
    if len(stripped) > 0:
        # 특수문자 비율
        special_ratio = len(re.sub(r'[a-zA-Z가-힣0-9]', '', stripped)) / len(stripped)
        if special_ratio > 0.7:
            report.score -= 20
            report.issues.append(QualityIssue("warning", "특수문자 비율이 높습니다. 검색에 적합하지 않을 수 있습니다."))

        # 숫자만
        digit_ratio = len(re.sub(r'[^0-9]', '', stripped)) / len(stripped)
        if digit_ratio > 0.8:
            report.score -= 15
            report.issues.append(QualityIssue("warning", "숫자 위주의 내용입니다. 설명 텍스트를 추가하면 검색이 잘 됩니다."))

    # 중복 감지 (DB 연결이 있을 때만)
    if db and tenant_id:
        try:
            duplicates = await _check_duplicates(content, tenant_id, db)
            if duplicates:
                report.score -= 15
                report.duplicate_docs = duplicates
                titles = ", ".join(d["title"] for d in duplicates[:3])
                report.issues.append(QualityIssue("warning", f"유사한 문서가 이미 존재합니다: {titles}"))
        except Exception as e:
            logger.warning(f"중복 감지 실패: {e}")

    report.score = max(0, report.score)

    # 점수별 제안
    if report.score >= 80 and not report.suggestions:
        report.suggestions.append("데이터 품질이 양호합니다.")
    elif report.score < 60:
        report.suggestions.append("데이터를 보완하면 검색 정확도가 크게 향상됩니다.")

    return report


async def _check_duplicates(content: str, tenant_id: str, db) -> list[dict]:
    """기존 문서와 유사도 비교하여 중복 감지 (별도 세션 사용)"""
    from sqlalchemy import text
    from app.core.db import get_session_maker

    # 업로드 문서의 임베딩 생성 (앞부분만 사용)
    sample = content[:1000]
    embedding = await create_embedding(sample)
    emb_str = "[" + ",".join(str(x) for x in embedding) + "]"

    # 별도 세션에서 실행 (기존 트랜잭션 오염 방지)
    async with get_session_maker()() as check_db:
        result = await check_db.execute(
            text(
                "SELECT d.id, d.title, 1 - (c.embedding <=> cast(:emb as vector)) as similarity "
                "FROM chunks c JOIN documents d ON c.document_id = d.id "
                "WHERE c.tenant_id = :tid "
                "ORDER BY c.embedding <=> cast(:emb as vector) LIMIT 5"
            ),
            {"emb": emb_str, "tid": tenant_id},
        )

        duplicates = []
        for row in result:
            if row.similarity > settings.duplicate_threshold:
                duplicates.append({
                    "id": row.id,
                    "title": row.title,
                    "similarity": round(float(row.similarity), 3),
                })

        return duplicates
