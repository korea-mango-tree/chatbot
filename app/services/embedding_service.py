import asyncio
import logging

from openai import AsyncOpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_MAX_BATCH = 2048  # OpenAI max texts per embeddings call
_MAX_RETRIES = 3


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def create_embedding(text: str) -> list[float]:
    response = await _get_client().embeddings.create(
        model=settings.embedding_model,
        input=text,
        dimensions=settings.embedding_dimension,
    )
    return response.data[0].embedding


async def create_embeddings(texts: list[str]) -> list[list[float]]:
    """Create embeddings for a list of texts.

    Automatically batches into groups of 2048 and retries up to 3 times
    with exponential backoff on transient failures.
    """
    client = _get_client()
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), _MAX_BATCH):
        batch = texts[i : i + _MAX_BATCH]
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                response = await client.embeddings.create(
                    model=settings.embedding_model,
                    input=batch,
                    dimensions=settings.embedding_dimension,
                )
                all_embeddings.extend(
                    item.embedding for item in response.data
                )
                last_error = None
                break
            except Exception as e:
                last_error = e
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "Embedding batch %d-%d failed (attempt %d/%d): %s — retrying in %ds",
                    i,
                    i + len(batch),
                    attempt + 1,
                    _MAX_RETRIES,
                    e,
                    wait,
                )
                await asyncio.sleep(wait)

        if last_error is not None:
            raise last_error

    return all_embeddings
