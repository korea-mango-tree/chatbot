from app.services.llm_service import _get_client
from app.core.config import get_settings

settings = get_settings()

SYSTEM_PROMPT = """당신은 SQL 파일을 분석해서 자연어로 설명하는 전문가입니다.

주어진 SQL 내용을 분석하여 다음 형식으로 작성하세요:

## 전체 요약
- 이 테이블의 목적과 비즈니스 의미를 2~3문장으로 요약

## 테이블 구조
- 테이블 이름과 각 컬럼의 의미를 설명

## 전체 데이터 목록
- INSERT 데이터가 있다면 **모든 레코드를 빠짐없이** 하나씩 설명
- 각 레코드는 핵심 필드(이름, 가격, 설명, 기능 등)를 모두 포함
- 절대 생략하지 말 것. "등" 이나 "외 N건" 같은 생략 표현 금지

## 데이터 비교/분류
- 데이터 간 차이점이나 분류 기준이 있으면 정리

한국어로 상세하게 작성하세요. SQL 코드는 포함하지 마세요."""


async def sql_to_description(sql_content: str, filename: str = "") -> str:
    client = _get_client()

    # SQL이 길면 분할 처리
    if len(sql_content) > 12000:
        return await _parse_long_sql(client, sql_content, filename)

    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"파일명: {filename}\n\nSQL 내용:\n{sql_content}"},
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    return response.choices[0].message.content


async def _parse_long_sql(client, sql_content: str, filename: str) -> str:
    """긴 SQL은 청크로 나눠서 각각 설명 생성 후 합침"""
    # SQL 문장 단위로 분할 (INSERT, CREATE 등)
    import re
    statements = re.split(r';\s*\n', sql_content)
    statements = [s.strip() for s in statements if s.strip()]

    descriptions = []
    batch = ""
    for stmt in statements:
        if len(batch) + len(stmt) > 10000:
            if batch:
                desc = await _parse_chunk(client, batch, filename)
                descriptions.append(desc)
            batch = stmt
        else:
            batch += ";\n" + stmt if batch else stmt

    if batch:
        desc = await _parse_chunk(client, batch, filename)
        descriptions.append(desc)

    return "\n\n---\n\n".join(descriptions)


async def _parse_chunk(client, sql_chunk: str, filename: str) -> str:
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"파일명: {filename}\n\nSQL 내용:\n{sql_chunk}"},
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    return response.choices[0].message.content
