from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.core.config import get_settings

settings = get_settings()


def get_chunks(content: str, source_type: str = "faq") -> list[str]:
    """Content-aware chunking based on source_type."""
    if source_type == "sql":
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=100,
            separators=["\n\n---\n\n", "\n\n## ", "\n\n", "\n", ". ", " ", ""],
        )
    elif source_type in ("document", "manual"):
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=100,
            separators=["\n\n## ", "\n\n### ", "\n\n", "\n", ". ", " ", ""],
        )
    else:  # faq, etc
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    return splitter.split_text(content)


def get_parent_child_chunks(
    content: str, source_type: str = "faq"
) -> tuple[list[str], list[dict]]:
    """Returns (parent_chunks, child_chunks_with_metadata)

    parent_chunks: list of large text chunks used for LLM context
    child_chunks_with_metadata: list of {
        "text": str,
        "parent_index": int,  # which parent this child belongs to
    }
    """
    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.parent_chunk_size,
        chunk_overlap=settings.parent_chunk_overlap,
        separators=["\n\n## ", "\n\n### ", "\n\n", "\n", ". ", " ", ""],
    )
    child_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.child_chunk_size,
        chunk_overlap=settings.child_chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    parent_chunks = parent_splitter.split_text(content)
    child_items: list[dict] = []

    for parent_index, parent_text in enumerate(parent_chunks):
        children = child_splitter.split_text(parent_text)
        for child_text in children:
            child_items.append({
                "text": child_text,
                "parent_index": parent_index,
            })

    return parent_chunks, child_items
