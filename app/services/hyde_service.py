"""HyDE (Hypothetical Document Embeddings) service.

Generates a hypothetical answer for a question so that its embedding
can be used alongside the original query embedding to improve retrieval.
"""

import logging
from pathlib import Path

from openai import AsyncOpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def generate_hypothetical_answer(question: str) -> str:
    """질문에 대한 가상 답변 생성 (HyDE)"""
    try:
        prompt_template = (PROMPT_DIR / "hyde_prompt.txt").read_text(encoding="utf-8")
        prompt = prompt_template.format(question=question)

        response = await _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=256,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error("HyDE generation failed: %s", e)
        return ""
