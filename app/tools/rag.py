import json

from langchain_core.tools import tool

from app.rag.retriever import search_rag_parent_chunks


@tool
async def search_knowledge_base(query: str) -> str:
    """Search the TenJudge knowledge base with a natural-language query.

    The knowledge base currently mainly contains information about the TenJudge platform.
    """
    chunks = await search_rag_parent_chunks("knowledge", query)
    return json.dumps(
        {
            "success": True,
            "query": query,
            "chunks": chunks,
            "count": len(chunks),
        },
        ensure_ascii=False,
    )
