from functools import lru_cache
from typing import Final, Literal

try:
    from langchain.retrievers import EnsembleRetriever
except ImportError:
    from langchain_classic.retrievers import EnsembleRetriever
import jieba
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGEngine, PGVectorStore

from app.core.config import settings
from app.core.db import pool

RagCollection = Literal["skill", "knowledge"]

BM25_K: Final = 5
VECTOR_K: Final = 5
RAG_SEARCH_K: Final = 5
RAG_COLLECTIONS: Final[tuple[RagCollection, ...]] = ("skill", "knowledge")
RAG_VECTOR_TABLES: Final[dict[RagCollection, str]] = {
    "skill": "rag_vector_skill",
    "knowledge": "rag_vector_knowledge",
}

bm25_retrievers: dict[RagCollection, BM25Retriever | None] = {
    "skill": None,
    "knowledge": None,
}
vector_stores: dict[RagCollection, PGVectorStore] = {}
vector_retrievers: dict[RagCollection, BaseRetriever | None] = {
    "skill": None,
    "knowledge": None,
}


@lru_cache
def get_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model="qwen/qwen3-embedding-8b",
        api_key=settings.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        max_retries=3,
    )


@lru_cache
def get_vector_engine() -> PGEngine:
    database_url = settings.DATABASE_URL
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return PGEngine.from_connection_string(database_url)


async def get_vector_store(collection: RagCollection) -> PGVectorStore:
    vector_store = vector_stores.get(collection)
    if vector_store is None:
        vector_store = await PGVectorStore.create(
            engine=get_vector_engine(),
            embedding_service=get_embeddings(),
            table_name=RAG_VECTOR_TABLES[collection],
            metadata_json_column="langchain_metadata",
        )
        vector_stores[collection] = vector_store
        vector_retrievers[collection] = vector_store.as_retriever(
            search_kwargs={"k": VECTOR_K}
        )

    return vector_store


async def init_vector_retrievers() -> None:
    for collection in RAG_COLLECTIONS:
        await get_vector_store(collection)


def update_bm25_retriever(
    collection: RagCollection,
    documents: list[Document],
) -> None:
    retriever = BM25Retriever.from_documents(
        documents,
        preprocess_func=lambda text: [
            token.strip().lower()
            for token in jieba.lcut(text)
            if token.strip()
        ],
    ) if documents else None
    if retriever:
        retriever.k = BM25_K

    bm25_retrievers[collection] = retriever


def get_bm25_retriever(collection: RagCollection) -> BM25Retriever | None:
    return bm25_retrievers[collection]


def get_vector_retriever(collection: RagCollection) -> BaseRetriever | None:
    return vector_retrievers[collection]


async def search_rag_parent_chunks(collection: RagCollection, query: str, k: int | None = None) -> list[str]:
    k = RAG_SEARCH_K if k is None else k
    if not query.strip() or k <= 0:
        return []

    retrievers = [
        retriever
        for retriever in (get_bm25_retriever(collection), get_vector_retriever(collection))
        if retriever is not None
    ]
    if not retrievers:
        return []

    documents = await EnsembleRetriever(
        retrievers=retrievers,
        weights=[1 / len(retrievers)] * len(retrievers),
    ).ainvoke(query)

    parent_chunk_ids: list[str] = []
    for document in documents:
        if (
            parent_chunk_id := document.metadata.get("parent_chunk_id")
        ) and parent_chunk_id not in parent_chunk_ids:
            parent_chunk_ids.append(parent_chunk_id)
    if not parent_chunk_ids:
        return []

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, content
                FROM rag_parent_chunks
                WHERE id = ANY(%s::uuid[])
                """,
                (parent_chunk_ids,),
            )
            rows = await cur.fetchall()

    contents = {str(parent_chunk_id): content for parent_chunk_id, content in rows}
    return [
        contents[parent_chunk_id]
        for parent_chunk_id in parent_chunk_ids
        if parent_chunk_id in contents
    ][:k]
