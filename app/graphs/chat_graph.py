from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.graph import StateGraph, START, END
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.llm_service import (
    _get_client,
    generate_answer,
    contextualize_question,
    generate_query_variations,
)
from app.services.hyde_service import generate_hypothetical_answer
from app.services.retrieval_service import search_chunks

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_answer_prompt() -> str:
    return (PROMPT_DIR / "answer_prompt.txt").read_text(encoding="utf-8")


@dataclass
class ChatState:
    question: str = ""
    chat_history: list = field(default_factory=list)
    rewritten_question: str = ""
    hyde_text: str = ""
    multi_queries: list = field(default_factory=list)
    retrieved_chunks: list[dict] = field(default_factory=list)
    confidence_score: float = 0.0
    answerable: bool = True
    answer: str = ""
    sources: list[dict] = field(default_factory=list)
    db: Any = None  # AsyncSession — not serialised
    tenant_id: str | None = None


# ── Nodes ──────────────────────────────────────────────


async def contextualize_question_node(state: ChatState) -> dict:
    question = state.question
    history = state.chat_history if state.chat_history else []
    rewritten = await contextualize_question(question, history)
    return {"rewritten_question": rewritten}


async def expand_queries_node(state: ChatState) -> dict:
    question = state.rewritten_question or state.question
    settings = get_settings()

    hyde_text = ""
    multi_queries: list[str] = []

    # Run HyDE and multi-query in parallel
    tasks = []
    task_labels: list[str] = []

    if settings.use_hyde:
        tasks.append(generate_hypothetical_answer(question))
        task_labels.append("hyde")
    if settings.use_multi_query:
        tasks.append(generate_query_variations(question, settings.multi_query_count))
        task_labels.append("multi_query")

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, label in enumerate(task_labels):
            if label == "hyde":
                if not isinstance(results[i], Exception):
                    hyde_text = results[i] or ""
                else:
                    logger.warning("HyDE failed: %s", results[i])
            elif label == "multi_query":
                if not isinstance(results[i], Exception):
                    multi_queries = results[i] or []
                else:
                    logger.warning("Multi-query failed: %s", results[i])

    return {"hyde_text": hyde_text, "multi_queries": multi_queries}


async def retrieve_chunks_node(state: ChatState) -> dict:
    query = state.rewritten_question or state.question
    chunks = await search_chunks(
        db=state.db,
        query=query,
        top_k=10,
        tenant_id=state.tenant_id,
        hyde_text=state.hyde_text if state.hyde_text else None,
        multi_queries=state.multi_queries if state.multi_queries else None,
    )
    return {"retrieved_chunks": chunks}


async def rerank_chunks_node(state: ChatState) -> dict:
    """Use LLM structured JSON output to rerank chunks by relevance."""
    chunks = state.retrieved_chunks
    if len(chunks) <= 3:
        # Too few chunks, assign default scores
        for c in chunks:
            c["relevance_score"] = 0.8
        return {"retrieved_chunks": chunks}

    question = state.rewritten_question or state.question

    # Build chunk list for LLM
    chunk_list = "\n".join(
        f"[{i}] {c['chunk_text'][:600]}" for i, c in enumerate(chunks)
    )

    # Load rerank prompt
    prompt_path = Path(__file__).parent.parent / "prompts" / "rerank_prompt.txt"
    prompt_template = prompt_path.read_text(encoding="utf-8")
    prompt = prompt_template.replace("{question}", question).replace("{chunks}", chunk_list)

    settings = get_settings()
    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=1024,
        )

        result = json.loads(response.choices[0].message.content)
        rankings = result.get("rankings", [])

        # Apply scores to chunks
        score_map = {r["index"]: r["score"] for r in rankings if "index" in r and "score" in r}
        for i, chunk in enumerate(chunks):
            chunk["relevance_score"] = score_map.get(i, 0.0)

        # Sort by score descending
        chunks.sort(key=lambda c: c.get("relevance_score", 0), reverse=True)

        # Filter out very low relevance (below 0.1)
        chunks = [c for c in chunks if c.get("relevance_score", 0) >= 0.1]

        # Cap at 10
        chunks = chunks[:10]

    except Exception as e:
        logger.warning(f"Reranking failed, using original order: {e}")
        for c in chunks:
            c["relevance_score"] = 0.5

    return {"retrieved_chunks": chunks}


async def score_confidence_node(state: ChatState) -> dict:
    """Compute confidence score from reranking results."""
    chunks = state.retrieved_chunks

    if not chunks:
        return {"confidence_score": 0.0}

    # Average of top 3 chunks' relevance scores
    top_scores = [c.get("relevance_score", 0) for c in chunks[:3]]
    avg_score = sum(top_scores) / len(top_scores) if top_scores else 0.0

    return {"confidence_score": round(avg_score, 3)}


def _is_greeting(text: str) -> bool:
    """인사말/간단한 대화인지 판별"""
    greetings = ["안녕", "안녕하세요", "반갑습니다", "하이", "헬로", "hi", "hello", "감사합니다", "고마워", "고맙습니다", "네", "알겠습니다", "알겠어요", "ㅎㅇ", "ㅎㅎ"]
    stripped = text.strip().lower().rstrip("?!.~")
    return stripped in greetings or len(stripped) <= 3


async def generate_answer_node(state: ChatState) -> dict:
    chunks = state.retrieved_chunks
    question = state.rewritten_question or state.question
    confidence = state.confidence_score if state.confidence_score else 0.0
    settings = get_settings()

    # 인사말은 신뢰도 체크 없이 바로 응답
    if _is_greeting(state.question):
        return {
            "answer": "안녕하세요! 무엇을 도와드릴까요? 궁금한 점이 있으시면 편하게 물어보세요.",
            "sources": [],
            "confidence_score": 1.0,
            "answerable": True,
        }

    # 검색 결과 없음 → 미답변
    if not chunks or confidence < settings.confidence_threshold:
        related = _get_unique_titles(chunks) if chunks else []
        answer = "해당 내용에 대한 정확한 답변을 찾을 수 없습니다. 담당자에게 문의가 전달되었습니다. 잠시만 기다려 주세요."
        if related:
            answer += "\n\n관련 있을 수 있는 주제:\n" + "\n".join(f"- {t}" for t in related[:5])

        return {"answer": answer, "sources": [], "answerable": False}

    # Build context from chunks (최대 ~4000자로 제한)
    context_parts = []
    context_len = 0
    for c in chunks:
        part = f"[문서: {c.get('document_title', '')}]\n{c['chunk_text']}"
        if context_len + len(part) > 8000:
            break
        context_parts.append(part)
        context_len += len(part)
    context = "\n\n---\n\n".join(context_parts)

    # Load prompt
    prompt_path = Path(__file__).parent.parent / "prompts" / "answer_prompt.txt"
    prompt_template = prompt_path.read_text(encoding="utf-8")
    prompt = prompt_template.replace("{context}", context).replace("{question}", question)

    if settings.use_chain_of_thought:
        # Structured output with CoT
        from app.services.llm_service import generate_answer_structured
        result = await generate_answer_structured(system_prompt="", user_message=prompt)
        answer = result["answer"]
        answerable = result.get("answerable", True)
        sources = result["sources"] or _get_unique_titles(chunks)
    else:
        # Legacy: simple answer
        answer = await generate_answer(system_prompt="", user_message=prompt)
        answerable = True
        sources = _get_unique_titles(chunks)

    return {"answer": answer, "sources": sources, "answerable": answerable}


def _get_unique_titles(chunks: list[dict]) -> list[str]:
    """Extract unique document titles from chunks"""
    seen = set()
    titles = []
    for c in chunks:
        title = c.get("document_title", "")
        if title and title not in seen:
            seen.add(title)
            titles.append(title)
    return titles


# ── Graph ──────────────────────────────────────────────


def build_chat_graph() -> StateGraph:
    graph = StateGraph(ChatState)
    graph.add_node("contextualize_question", contextualize_question_node)
    graph.add_node("expand_queries", expand_queries_node)
    graph.add_node("retrieve_chunks", retrieve_chunks_node)
    graph.add_node("rerank_chunks", rerank_chunks_node)
    graph.add_node("score_confidence", score_confidence_node)
    graph.add_node("generate_answer", generate_answer_node)

    graph.add_edge(START, "contextualize_question")
    graph.add_edge("contextualize_question", "expand_queries")
    graph.add_edge("expand_queries", "retrieve_chunks")
    graph.add_edge("retrieve_chunks", "rerank_chunks")
    graph.add_edge("rerank_chunks", "score_confidence")
    graph.add_edge("score_confidence", "generate_answer")
    graph.add_edge("generate_answer", END)

    return graph.compile()


chat_graph = build_chat_graph()
