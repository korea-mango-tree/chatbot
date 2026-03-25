from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://chatbot:chatbot1234@localhost:5432/chatbot_db"

    # OpenAI
    openai_api_key: str = ""

    # Embedding
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536

    # LLM
    llm_model: str = "gpt-4o-mini"

    # Chunk (기본값, FAQ용)
    chunk_size: int = 800
    chunk_overlap: int = 100

    # Auth
    jwt_secret: str = "chatbot-secret-key-change-in-production"
    jwt_expire_hours: int = 24
    admin_default_username: str = "admin"
    admin_default_password: str = "admin1234"

    # ─── Pinecone ───
    pinecone_api_key: str = ""
    pinecone_index_name: str = "chatbot-rag"
    vector_store: str = "both"  # "pgvector" | "pinecone" | "both"

    # ─── 검색 전략 ───
    use_hyde: bool = True
    use_multi_query: bool = True
    multi_query_count: int = 3
    confidence_threshold: float = 0.5

    # ─── 리랭킹 ───
    reranker_type: str = "llm_structured"  # "llm_structured" | "cohere"
    cohere_api_key: str = ""

    # ─── 청킹 ───
    chunking_strategy: str = "parent_child"  # "recursive" | "parent_child"
    parent_chunk_size: int = 2000
    parent_chunk_overlap: int = 200
    child_chunk_size: int = 400
    child_chunk_overlap: int = 50

    # ─── 답변 생성 ───
    use_chain_of_thought: bool = True
    answer_llm_model: str = "gpt-4o"  # 답변용 별도 모델

    # ─── 데이터 품질 ───
    auto_preprocess: bool = True
    auto_generate_metadata: bool = True
    duplicate_threshold: float = 0.9
    min_content_length: int = 50
    max_content_length: int = 50000

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
