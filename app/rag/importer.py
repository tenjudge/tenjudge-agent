import hashlib
import uuid
from pathlib import Path
from typing import Literal

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings
from app.core.db import pool
from app.rag.retriever import (
    get_vector_store,
    init_vector_retrievers,
    update_bm25_retriever,
)

RagCollection = Literal["skill", "knowledge"]

parent_splitter = RecursiveCharacterTextSplitter(
    chunk_size=3000,  # 每块最大字符数
    chunk_overlap=300,  # 相邻块重叠字符数
)
child_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=100,
)


def calculate_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def import_vector(document_id: uuid.UUID, collection: RagCollection, document_content: str):
    # 1. 先切 parent chunks，并保存到数据库。
    vector_documents: list[Document] = []
    vector_ids: list[str] = []
    parent_chunks = [
        chunk for chunk in parent_splitter.split_text(document_content)
        if chunk.strip()
    ]
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for parent_content in parent_chunks:
                parent_chunk_id = uuid.uuid7()
                await cur.execute(
                    """
                    INSERT INTO rag_parent_chunks (id, document_id, content)
                    VALUES (%s, %s, %s)
                    """,
                    (parent_chunk_id, document_id, parent_content),
                )

                # 2. 每个 parent chunk 再切 child chunks，child 存入 PGVector。
                for child_content in child_splitter.split_text(parent_content):
                    if not child_content.strip():
                        continue
                    child_chunk_id = uuid.uuid7()
                    vector_ids.append(str(child_chunk_id))
                    vector_documents.append(
                        Document(
                            page_content=child_content,
                            metadata={
                                "document_id": str(document_id),
                                "parent_chunk_id": str(parent_chunk_id),
                            },
                        )
                    )

    # 3. 异步写入向量数据库
    if vector_documents:
        vector_store = await get_vector_store(collection)
        await vector_store.aadd_documents(vector_documents, ids=vector_ids)


async def delete_vector(document_id: uuid.UUID, collection: RagCollection):
    vector_store = await get_vector_store(collection)
    await vector_store.adelete(
        ids=None,
        filter={"document_id": {"$eq": str(document_id)}},
    )


async def _delete_document(document_id: uuid.UUID, collection: RagCollection):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM rag_parent_chunks WHERE document_id = %s",
                (document_id,),
            )
            await cur.execute(
                "DELETE FROM rag_documents WHERE id = %s",
                (document_id,),
            )
    await delete_vector(document_id, collection)


async def rebuild_bm25():
    documents = {
        "skill": [],
        "knowledge": [],
    }

    # 1. BM25 使用 parent chunks，全量读库后重建内存索引。
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT d.collection, p.document_id, p.id, p.content
                FROM rag_parent_chunks p
                JOIN rag_documents d ON d.id = p.document_id
                """
            )
            rows = await cur.fetchall()

    # 2. metadata 和向量检索保持一致，方便后面统一按 parent_chunk_id 去重。
    for collection, document_id, parent_chunk_id, content in rows:
        documents[collection].append(
            Document(
                page_content=content,
                metadata={
                    "document_id": str(document_id),
                    "parent_chunk_id": str(parent_chunk_id),
                },
            )
        )

    update_bm25_retriever("skill", documents["skill"])
    update_bm25_retriever("knowledge", documents["knowledge"])


async def import_rag_directory(root_path: str, collection: RagCollection) -> None:
    if not root_path:
        return

    root = Path(root_path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"RAG directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"RAG path is not a directory: {root}")

    # 1. 先递归读取当前目录下所有文件，用 sha256 表示“当前应该存在的文档集合”。
    current_files: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        content = path.read_text(encoding="utf-8")
        sha256 = calculate_sha256(content)
        current_files[sha256] = content

    # 2. 读取数据库中这个 collection 已经导入过的文档。
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, sha256 FROM rag_documents WHERE collection = %s",
                (collection,),
            )
            rows = await cur.fetchall()
    db_docs = {sha256: document_id for document_id, sha256 in rows}

    # 3. 删除目录中已经不存在的文档；每个文档使用一个小事务。
    removed_ids = [
        document_id
        for sha256, document_id in db_docs.items()
        if sha256 not in current_files
    ]
    for document_id in removed_ids:
        await _delete_document(document_id, collection)

    # 4. 写入新文档
    for sha256, content in current_files.items():
        if sha256 in db_docs:
            continue

        document_id = uuid.uuid7()
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO rag_documents (id, collection, sha256)
                        VALUES (%s, %s, %s)
                        """,
                        (document_id, collection, sha256),
                    )
            await import_vector(document_id, collection, content)
        except Exception:
            await _delete_document(document_id, collection)
            raise


async def startup_rag() -> None:
    if not settings.RAG_SKILL_DIR and not settings.RAG_KNOWLEDGE_DIR:
        return

    await init_vector_retrievers()
    await import_rag_directory(settings.RAG_SKILL_DIR, "skill")
    await import_rag_directory(settings.RAG_KNOWLEDGE_DIR, "knowledge")
    await rebuild_bm25()
