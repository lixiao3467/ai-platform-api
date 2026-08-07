"""Knowledge engine — RAG pipeline core."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.config import get_settings
from ai_platform.core.knowledge.chunkers.recursive import RecursiveChunker
from ai_platform.core.knowledge.parsers.base import parse_document
from ai_platform.domain.models import Document, DocumentChunk, KnowledgeBase

logger = structlog.get_logger()


@dataclass
class RetrievedChunk:
    """A chunk retrieved from the knowledge base."""

    content: str
    score: float
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    metadata: dict


class KnowledgeEngine:
    """
    RAG Pipeline — document ingestion and knowledge retrieval.

    Ingest: file → parse → chunk → embed → store (Milvus + PostgreSQL)
    Query:  question → embed → vector search → rerank → return chunks
    """

    def __init__(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID | None = None,
    ) -> None:
        self._db = session
        self._tenant_id = tenant_id
        self._chunker = RecursiveChunker(chunk_size=512, chunk_overlap=64)

    async def ingest_document(
        self,
        document: Document,
        file_content: bytes,
        kb: KnowledgeBase,
    ) -> int:
        """
        Process and ingest a document into the knowledge base.

        Returns the number of chunks created.
        """
        logger.info("Ingesting document", doc_id=str(document.id), filename=document.filename)

        # 1. Parse document to text
        try:
            text = await parse_document(file_content, document.mime_type or "")
        except Exception as e:
            document.status = "failed"
            document.error_message = f"Parse error: {e}"
            await self._db.flush()
            logger.error("Document parse failed", doc_id=str(document.id), error=str(e))
            return 0

        if not text.strip():
            document.status = "failed"
            document.error_message = "Document is empty after parsing"
            await self._db.flush()
            return 0

        # 2. Chunk the text (use KB-specific config when available)
        chunk_config = kb.chunk_config or {}
        chunker = RecursiveChunker(
            chunk_size=chunk_config.get("chunk_size", 512),
            chunk_overlap=chunk_config.get("chunk_overlap", 64),
        )
        chunks = chunker.chunk(text, metadata={
            "document_id": str(document.id),
            "filename": document.filename,
        })

        # 3. Generate embeddings and store in Milvus
        from ai_platform.core.knowledge.embeddings.embedder import create_embedder
        from ai_platform.core.knowledge.store.milvus_store import get_milvus_store

        embedder = await create_embedder(self._tenant_id, self._db)

        # Batch embedding for efficiency
        texts = [c.content for c in chunks]
        embeddings = await embedder.embed_batch(texts)

        # Derive vector dimension from actual embedding output so the Milvus
        # collection is created with the correct dimension for this model.
        dim = len(embeddings[0]) if embeddings else None
        milvus = await get_milvus_store(kb.collection_name, kb.embedding_model, dim=dim)

        chunk_records = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):

            # Store in Milvus
            vector_id = f"{document.id}_{i}"
            await milvus.insert(
                collection_name=kb.collection_name,
                vector_id=vector_id,
                embedding=embedding,
                metadata={
                    "chunk_id": str(uuid.uuid4()),
                    "document_id": str(document.id),
                    "kb_id": str(kb.id),
                    "chunk_index": i,
                    "content": chunk.content,
                },
            )

            # Create PostgreSQL record
            chunk_record = DocumentChunk(
                id=uuid.uuid4(),
                document_id=document.id,
                kb_id=kb.id,
                content=chunk.content,
                chunk_index=i,
                token_count=len(chunk.content) // 4,  # rough estimate
                metadata_=chunk.metadata,
                vector_id=vector_id,
            )
            chunk_records.append(chunk_record)

        # 4. Bulk insert chunks to PostgreSQL
        self._db.add_all(chunk_records)

        # 5. Update document and KB stats
        document.status = "ready"
        document.chunk_count = len(chunk_records)
        kb.doc_count += 1
        kb.chunk_count += len(chunk_records)
        await self._db.flush()

        logger.info(
            "Document ingested",
            doc_id=str(document.id),
            chunks=len(chunk_records),
        )
        return len(chunk_records)

    async def query(
        self,
        question: str,
        kb_ids: list[uuid.UUID],
        *,
        top_k: int = 5,
        score_threshold: float = 0.3,
    ) -> list[RetrievedChunk]:
        """
        Retrieve relevant chunks from knowledge bases.

        1. Embed the query
        2. Vector search in Milvus
        3. Filter by score threshold
        4. Return ranked chunks
        """
        from ai_platform.core.knowledge.embeddings.embedder import create_embedder
        from ai_platform.core.knowledge.store.milvus_store import get_milvus_store

        embedder = await create_embedder(self._tenant_id, self._db)
        query_embedding = await embedder.embed(question)

        all_results = []

        for kb_id in kb_ids:
            kb = await self._db.get(KnowledgeBase, kb_id)
            if not kb:
                continue

            milvus = await get_milvus_store(kb.collection_name, kb.embedding_model)
            results = await milvus.search(
                collection_name=kb.collection_name,
                query_embedding=query_embedding,
                top_k=top_k,
            )

            for hit in results:
                score = hit.get("score", 0)
                if score < score_threshold:
                    continue

                meta = hit.get("metadata", {})
                all_results.append(
                    RetrievedChunk(
                        content=meta.get("content", ""),
                        score=score,
                        document_id=uuid.UUID(meta.get("document_id", str(uuid.uuid4()))),
                        chunk_id=uuid.UUID(meta.get("chunk_id", str(uuid.uuid4()))),
                        metadata=meta,
                    )
                )

        # Sort by score descending
        all_results.sort(key=lambda x: x.score, reverse=True)
        return all_results[:top_k]
