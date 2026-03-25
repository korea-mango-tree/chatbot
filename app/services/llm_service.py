import json
import logging
from pathlib import Path

from openai import AsyncOpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def generate_answer(system_prompt: str, user_message: str) -> str:
    response = await _get_client().chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.3,
        max_tokens=1024,
    )
    return response.choices[0].message.content


async def contextualize_question(question: str, chat_history: list[dict]) -> str:
    """대화 이력을 반영하여 독립적인 질문으로 재작성"""
    if not chat_history:
        return question

    try:
        prompt_template = (PROMPT_DIR / "contextualize_prompt.txt").read_text(encoding="utf-8")
        history_str = "\n".join(
            f"{m.get('role', 'user')}: {m.get('message', '')}" for m in chat_history
        )
        prompt = prompt_template.format(chat_history=history_str, question=question)

        response = await _get_client().chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
        )
        result = response.choices[0].message.content
        return result.strip() if result else question
    except Exception as e:
        logger.error("contextualize_question failed: %s", e)
        return question


async def generate_query_variations(question: str, n: int = 3) -> list[str]:
    """같은 질문의 다른 표현 N개 생성"""
    try:
        prompt = (
            f"다음 질문을 {n}가지 다른 표현으로 재작성하세요. "
            f'반드시 JSON 객체로 출력하세요. "queries" 키에 배열을 넣으세요: '
            f'{{"queries": ["변형1", "변형2", ...]}}\n\n질문: {question}'
        )

        response = await _get_client().chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        parsed = json.loads(content)
        # Support both "queries" and other possible keys
        if "queries" in parsed:
            return parsed["queries"]
        # Fallback: try first list value in the JSON
        for v in parsed.values():
            if isinstance(v, list):
                return v
        return []
    except Exception as e:
        logger.error("generate_query_variations failed: %s", e)
        return []


async def generate_answer_structured(system_prompt: str, user_message: str) -> dict:
    """Generate answer with structured JSON output using gpt-4o.

    Returns: {"thinking": str, "answerable": bool, "answer": str, "confidence": float, "sources": list[str]}
    """
    settings = get_settings()
    client = _get_client()

    try:
        response = await client.chat.completions.create(
            model=settings.answer_llm_model,  # gpt-4o
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=2048,
        )

        result = json.loads(response.choices[0].message.content)
        confidence = result.get("confidence", 0.5)
        answerable = result.get("answerable", confidence >= 0.5)
        return {
            "thinking": result.get("thinking", ""),
            "answerable": bool(answerable),
            "answer": result.get("answer", response.choices[0].message.content),
            "confidence": confidence,
            "sources": result.get("sources", []),
        }
    except json.JSONDecodeError:
        return {
            "thinking": "",
            "answerable": False,
            "answer": response.choices[0].message.content,
            "confidence": 0.5,
            "sources": [],
        }
    except Exception as e:
        logger.error(f"Structured answer generation failed: {e}")
        return {
            "thinking": "",
            "answerable": False,
            "answer": "죄송합니다. 답변 생성 중 오류가 발생했습니다.",
            "confidence": 0.0,
            "sources": [],
        }


async def rewrite_question(question: str) -> str:
    response = await _get_client().chat.completions.create(
        model=settings.llm_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 사용자의 질문을 검색에 최적화된 형태로 다시 작성하는 도우미입니다. "
                    "핵심 키워드를 유지하면서, 검색에 적합한 간결한 문장으로 변환하세요. "
                    "변환된 질문만 출력하세요."
                ),
            },
            {"role": "user", "content": question},
        ],
        temperature=0.0,
        max_tokens=256,
    )
    return response.choices[0].message.content
