CREATE TABLE IF NOT EXISTS rag_documents (
    id UUID PRIMARY KEY,
    collection VARCHAR(32) NOT NULL,
    sha256 CHAR(64) NOT NULL,

    CHECK (collection IN ('skill', 'knowledge')),
    UNIQUE (collection, sha256)
);


CREATE TABLE IF NOT EXISTS rag_parent_chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL,
    content TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rag_parent_chunks_document
    ON rag_parent_chunks (document_id);


CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_vector_skill (
    langchain_id UUID PRIMARY KEY,
    content TEXT NOT NULL,
    embedding vector(4096) NOT NULL,
    langchain_metadata JSON
);

CREATE TABLE IF NOT EXISTS rag_vector_knowledge (
    langchain_id UUID PRIMARY KEY,
    content TEXT NOT NULL,
    embedding vector(4096) NOT NULL,
    langchain_metadata JSON
);
