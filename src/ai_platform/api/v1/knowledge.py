"""Knowledge Bases API — /api/v1/knowledge-bases/*."""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.chat import ChatCompletionRequest, ChatMessage
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.api.v1._shared import IdRequest
from ai_platform.config import get_settings
from ai_platform.core.knowledge.chunkers.recursive import RecursiveChunker
from ai_platform.core.knowledge.engine import KnowledgeEngine
from ai_platform.core.knowledge.parsers.base import parse_document
from ai_platform.core.model_router.litellm_client import get_llm_client
from ai_platform.domain.models import Document, DocumentChunk, KnowledgeBase, KnowledgeGroup
from ai_platform.infra.database.connection import get_db, get_session_factory

logger = structlog.get_logger()

router = APIRouter()

# Keep strong references to background tasks so they aren't GC'd mid-run.
_background_tasks: set[asyncio.Task] = set()


# =============================================================================
# Schemas
# =============================================================================


class KBCreateRequest(BaseModel):
    name: str = Field(max_length=128, min_length=1)
    description: str | None = Field(default=None, max_length=1000)
    embedding_model: str = Field(default="text-embedding-3-small", max_length=100)
    chunk_size: int = Field(default=512, ge=100, le=2000)
    chunk_overlap: int = Field(default=64, ge=0, le=500)
    group_id: str | None = Field(default=None, description="知识库分组 ID")

    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate KB name - reject suspicious patterns."""
        # Reject SQL-like patterns
        suspicious = ["'", '"', ";", "--", "/*", "*/"]
        if any(pattern in v for pattern in suspicious):
            raise ValueError("Name contains invalid characters")
        return v


class KBOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    embedding_model: str
    doc_count: int
    chunk_count: int
    status: str
    group_id: str | None
    created_at: str


class DocOut(BaseModel):
    id: uuid.UUID
    filename: str
    mime_type: str | None
    file_size: int | None
    chunk_count: int
    status: str
    error_message: str | None
    file_hash: str | None = None
    parse_result_path: str | None = None
    processing_progress: dict | None = None
    created_at: str


class KBListRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    group_id: str | None = Field(default=None, description="按分组过滤")


class KBUpdateBody(BaseModel):
    id: str
    name: str | None = Field(default=None, max_length=128, min_length=1)
    description: str | None = Field(default=None, max_length=1000)
    embedding_model: str | None = Field(default=None, max_length=100)
    group_id: str | None = Field(default=None, description="知识库分组 ID")


class KBQueryBody(BaseModel):
    kb_id: str
    question: str = Field(max_length=10000, min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    score_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    generate_answer: bool = Field(default=True, description="Use LLM to generate answer from chunks")
    model: str = Field(default="gpt-4o", max_length=100)


class KBDocIdRequest(BaseModel):
    kb_id: str
    id: str


class KBDocListRequest(BaseModel):
    kb_id: str


class RetrievedChunkOut(BaseModel):
    content: str
    score: float
    document_id: str
    source: str | None = None


# =============================================================================
# Helpers
# =============================================================================


def _doc_to_out(doc: Document) -> DocOut:
    """Build a DocOut from a Document ORM instance."""
    return DocOut(
        id=doc.id,
        filename=doc.filename,
        mime_type=doc.mime_type,
        file_size=doc.file_size,
        chunk_count=doc.chunk_count or 0,
        status=doc.status or "pending",
        error_message=doc.error_message,
        file_hash=doc.file_hash,
        parse_result_path=doc.parse_result_path,
        processing_progress=doc.processing_progress,
        created_at=doc.created_at.isoformat(),
    )


def _cleanup_document_files(doc: Document) -> None:
    """Remove raw file and parse-result cache from disk (best-effort)."""
    for path_str in (doc.storage_path, doc.parse_result_path):
        if not path_str:
            continue
        try:
            p = Path(path_str)
            if p.exists():
                p.unlink()
        except Exception as e:
            logger.warning("Failed to cleanup document file", path=path_str, error=str(e))


async def _process_document(
    doc_id: uuid.UUID,
    kb_id: uuid.UUID,
    content: bytes,
    filename: str,
    mime_type: str,
    tenant_id: uuid.UUID | None = None,
) -> None:
    """Background task: parse → chunk → embed → store.

    Runs outside the request lifecycle with its own DB session.
    """
    factory = get_session_factory()
    async with factory() as db:
        doc = await db.get(Document, doc_id)
        kb = await db.get(KnowledgeBase, kb_id)
        if not doc or not kb:
            logger.error("Background task: doc/kb not found", doc_id=str(doc_id), kb_id=str(kb_id))
            return

        try:
            # --- Parse ---
            doc.processing_progress = {"stage": "parsing", "percent": 10, "message": "解析文档中..."}
            await db.commit()

            text = await parse_document(content, mime_type)
            if not text.strip():
                raise RuntimeError("Document is empty after parsing")

            # Persist parsed markdown cache
            parse_result_path = Path(doc.storage_path).with_suffix(".md") if doc.storage_path else None
            if parse_result_path:
                parse_result_path.parent.mkdir(parents=True, exist_ok=True)
                parse_result_path.write_text(text, encoding="utf-8")
                doc.parse_result_path = str(parse_result_path)

            doc.processing_progress = {"stage": "chunking", "percent": 30, "message": "分块中..."}
            await db.commit()

            # --- Chunk (use KB config) ---
            chunk_config = kb.chunk_config or {}
            chunker = RecursiveChunker(
                chunk_size=chunk_config.get("chunk_size", 512),
                chunk_overlap=chunk_config.get("chunk_overlap", 64),
            )
            chunks = chunker.chunk(text, metadata={"document_id": str(doc.id), "filename": filename})

            doc.processing_progress = {
                "stage": "embedding",
                "percent": 50,
                "message": f"向量化中 (0/{len(chunks)})...",
            }
            await db.commit()

            # --- Embed (batch) + Store ---
            from ai_platform.core.knowledge.embeddings.embedder import create_embedder
            from ai_platform.core.knowledge.store.milvus_store import get_milvus_store

            embedder = await create_embedder(tenant_id, db)
            milvus = await get_milvus_store(kb.collection_name, kb.embedding_model)

            texts = [c.content for c in chunks]
            embeddings = await embedder.embed_batch(texts)

            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                vector_id = f"{doc.id}_{chunk.metadata.get('chunk_index', i)}"
                await milvus.insert(
                    collection_name=kb.collection_name,
                    vector_id=vector_id,
                    embedding=embedding,
                    metadata={
                        "chunk_id": str(uuid.uuid4()),
                        "document_id": str(doc.id),
                        "kb_id": str(kb.id),
                        "chunk_index": chunk.metadata.get("chunk_index", i),
                        "content": chunk.content,
                    },
                )

                db_chunk = DocumentChunk(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    kb_id=kb.id,
                    content=chunk.content,
                    chunk_index=chunk.metadata.get("chunk_index", i),
                    token_count=len(chunk.content) // 4,
                    metadata_=chunk.metadata,
                    vector_id=vector_id,
                )
                db.add(db_chunk)

                # Progress update every chunk, commit every 10
                percent = 50 + int(40 * (i + 1) / len(chunks))
                doc.processing_progress = {
                    "stage": "embedding",
                    "percent": percent,
                    "message": f"向量化中 ({i + 1}/{len(chunks)})...",
                }
                if (i + 1) % 10 == 0:
                    await db.commit()

            # --- Finalize ---
            doc.status = "ready"
            doc.chunk_count = len(chunks)
            doc.processing_progress = {"stage": "completed", "percent": 100, "message": "处理完成"}
            doc.error_message = None

            # Atomic counter update (avoids read-modify-write race)
            await db.execute(
                sa_update(KnowledgeBase)
                .where(KnowledgeBase.id == kb.id)
                .values(
                    doc_count=KnowledgeBase.doc_count + 1,
                    chunk_count=KnowledgeBase.chunk_count + len(chunks),
                )
            )

            await db.commit()
            logger.info("Document processed", doc_id=str(doc.id), chunks=len(chunks))

        except Exception as e:
            logger.exception("Document processing failed", doc_id=str(doc_id))
            doc.status = "failed"
            doc.error_message = str(e)[:2000]
            doc.processing_progress = {"stage": "failed", "percent": 0, "message": f"处理失败: {str(e)[:200]}"}
            await db.commit()


# =============================================================================
# Knowledge Base CRUD
# =============================================================================


@router.post("/create", response_model=ApiResponse[KBOut], dependencies=[Depends(require_permission("knowledge.write"))])
async def create_knowledge_base(
    req: KBCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Create a new knowledge base."""
    collection_name = f"kb_{uuid.uuid4().hex[:12]}"

    # Validate group_id if provided
    group_id_uuid = None
    if req.group_id:
        group_id_uuid = uuid.UUID(req.group_id)
        group = await session.get(KnowledgeGroup, group_id_uuid)
        if not group or group.tenant_id != ctx.tenant_id:
            raise HTTPException(status_code=404, detail="知识库分组不存在")

    kb = KnowledgeBase(
        id=uuid.uuid4(),
        app_id=ctx.app_id,
        tenant_id=ctx.tenant_id,
        group_id=group_id_uuid,
        name=req.name,
        description=req.description,
        embedding_model=req.embedding_model,
        chunk_config={"chunk_size": req.chunk_size, "chunk_overlap": req.chunk_overlap},
        collection_name=collection_name,
    )
    session.add(kb)
    await session.flush()

    return ApiResponse(data=KBOut(
        id=kb.id, name=kb.name, description=kb.description,
        embedding_model=kb.embedding_model, doc_count=0, chunk_count=0,
        status=kb.status, group_id=str(kb.group_id) if kb.group_id else None,
        created_at=kb.created_at.isoformat(),
    ))


@router.post("/list", response_model=ApiResponse[PaginatedResponse[KBOut]], dependencies=[Depends(require_permission("knowledge.read"))])
async def list_knowledge_bases(
    req: KBListRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List knowledge bases for the current tenant."""
    page = req.page
    page_size = req.page_size
    group_id = req.group_id
    offset = (page - 1) * page_size

    conditions = [KnowledgeBase.tenant_id == ctx.tenant_id]
    if group_id:
        group_uuid = uuid.UUID(group_id)
        conditions.append(KnowledgeBase.group_id == group_uuid)

    query = (
        select(KnowledgeBase)
        .where(*conditions)
        .order_by(KnowledgeBase.created_at.desc())
        .offset(offset).limit(page_size)
    )
    result = await session.execute(query)
    kbs = result.scalars().all()

    total = (await session.execute(
        select(func.count()).select_from(KnowledgeBase).where(*conditions)
    )).scalar() or 0

    items = [
        KBOut(id=kb.id, name=kb.name, description=kb.description,
              embedding_model=kb.embedding_model, doc_count=kb.doc_count,
              chunk_count=kb.chunk_count, status=kb.status,
              group_id=str(kb.group_id) if kb.group_id else None,
              created_at=kb.created_at.isoformat())
        for kb in kbs
    ]
    return ApiResponse(data=PaginatedResponse(items=items, total=total, page=page, page_size=page_size))


@router.post("/get", response_model=ApiResponse[KBOut], dependencies=[Depends(require_permission("knowledge.read"))])
async def get_knowledge_base(
    req: IdRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get knowledge base details."""
    kb_id = uuid.UUID(req.id)
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return ApiResponse(data=KBOut(
        id=kb.id, name=kb.name, description=kb.description,
        embedding_model=kb.embedding_model, doc_count=kb.doc_count,
        chunk_count=kb.chunk_count, status=kb.status,
        group_id=str(kb.group_id) if kb.group_id else None,
        created_at=kb.created_at.isoformat(),
    ))


@router.post("/update", response_model=ApiResponse[KBOut], dependencies=[Depends(require_permission("knowledge.write"))])
async def update_knowledge_base(
    body: KBUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Update knowledge base metadata."""
    kb_id = uuid.UUID(body.id)
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    updates = body.model_dump(exclude_unset=True, exclude={"id"})
    if "group_id" in updates and updates["group_id"] is not None:
        updates["group_id"] = uuid.UUID(updates["group_id"])

    for k, v in updates.items():
        setattr(kb, k, v)
    await session.commit()
    await session.refresh(kb)

    return ApiResponse(data=KBOut(
        id=kb.id, name=kb.name, description=kb.description,
        embedding_model=kb.embedding_model, doc_count=kb.doc_count,
        chunk_count=kb.chunk_count, status=kb.status,
        group_id=str(kb.group_id) if kb.group_id else None,
        created_at=kb.created_at.isoformat(),
    ))


@router.post("/delete", response_model=ApiResponse, dependencies=[Depends(require_permission("knowledge.write"))])
async def delete_knowledge_base(
    req: IdRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Delete a knowledge base and all its documents/chunks."""
    kb_id = uuid.UUID(req.id)
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # Clean up Milvus collection first
    try:
        from ai_platform.core.knowledge.store.milvus_store import get_milvus_store
        store = await get_milvus_store(kb.collection_name, kb.embedding_model)
        await store.delete_collection(kb.collection_name)
    except Exception as e:
        logger.warning("Milvus cleanup failed during KB delete", kb_id=str(kb_id), error=str(e))

    # Clean up stored files for all documents in this KB
    docs_result = await session.execute(select(Document).where(Document.kb_id == kb_id))
    for doc in docs_result.scalars().all():
        _cleanup_document_files(doc)

    await session.delete(kb)
    return ApiResponse(message="Knowledge base deleted")


# =============================================================================
# Document Management
# =============================================================================


@router.post("/documents/upload", response_model=ApiResponse[DocOut], dependencies=[Depends(require_permission("knowledge.write"))])
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form(..., description="Knowledge base ID"),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Upload a document. Processing happens in the background."""
    kb_uuid = uuid.UUID(kb_id)
    kb = await session.get(KnowledgeBase, kb_uuid)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # 1. Read file content (with size limit to avoid OOM)
    MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大，最大 {MAX_UPLOAD_SIZE // 1024 // 1024}MB",
        )

    # 2. Compute file hash (dedup)
    file_hash = hashlib.sha256(content).hexdigest()

    # 3. Persist raw file to disk (sanitize filename to prevent path traversal)
    settings = get_settings()
    storage_dir = Path(settings.storage_path) / "documents" / str(ctx.tenant_id)
    storage_dir.mkdir(parents=True, exist_ok=True)
    doc_id = uuid.uuid4()
    safe_filename = re.sub(r'[^\w\s\-.]', '_', file.filename or 'unknown')
    safe_filename = safe_filename.strip()[:200]
    if not safe_filename:
        safe_filename = 'unknown'
    storage_path = storage_dir / f"{doc_id}_{safe_filename}"
    storage_path.write_bytes(content)

    # 4. Create Document record (status=processing)
    doc = Document(
        id=doc_id,
        kb_id=kb.id,
        filename=file.filename or "unknown",
        mime_type=file.content_type or "text/plain",
        file_size=len(content),
        storage_path=str(storage_path),
        file_hash=file_hash,
        status="processing",
        processing_progress={"stage": "queued", "percent": 0, "message": "等待处理"},
    )
    session.add(doc)
    await session.flush()
    await session.refresh(doc)

    # Commit BEFORE launching the task so the background session can see the row
    await session.commit()

    # 5. Kick off background processing (own DB session)
    task = asyncio.create_task(
        _process_document(
            doc.id,
            kb.id,
            content,
            doc.filename,
            doc.mime_type or "text/plain",
            tenant_id=ctx.tenant_id,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    # 6. Return immediately
    return ApiResponse(data=_doc_to_out(doc))


@router.post("/documents/list", response_model=ApiResponse[list[DocOut]], dependencies=[Depends(require_permission("knowledge.read"))])
async def list_documents(
    req: KBDocListRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List documents in a knowledge base."""
    kb_id = uuid.UUID(req.kb_id)
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    stmt = select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at.desc())
    result = await session.execute(stmt)
    docs = result.scalars().all()

    return ApiResponse(data=[_doc_to_out(d) for d in docs])


@router.post("/documents/get", response_model=ApiResponse[DocOut], dependencies=[Depends(require_permission("knowledge.read"))])
async def get_document(
    req: KBDocIdRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get document details including processing progress."""
    kb_id = uuid.UUID(req.kb_id)
    doc_id = uuid.UUID(req.id)
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    doc = await session.get(Document, doc_id)
    if not doc or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="Document not found")

    return ApiResponse(data=_doc_to_out(doc))


@router.post("/documents/retry", response_model=ApiResponse[DocOut], dependencies=[Depends(require_permission("knowledge.write"))])
async def retry_document(
    req: KBDocIdRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Retry processing a failed document."""
    kb_id = uuid.UUID(req.kb_id)
    doc_id = uuid.UUID(req.id)
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    doc = await session.get(Document, doc_id)
    if not doc or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status != "failed":
        raise HTTPException(status_code=400, detail="Only failed documents can be retried")

    if not doc.storage_path or not Path(doc.storage_path).exists():
        raise HTTPException(status_code=400, detail="Original file not found on disk; cannot retry")

    # Count old chunks so we can decrement KB counters accurately
    old_chunks_result = await session.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
    )
    old_chunks = old_chunks_result.scalars().all()
    old_chunk_count = len(old_chunks)

    # Delete old PG chunk records
    for chunk in old_chunks:
        await session.delete(chunk)

    # Clean up old vectors in Milvus (best-effort)
    try:
        from ai_platform.core.knowledge.store.milvus_store import get_milvus_store
        store = await get_milvus_store(kb.collection_name, kb.embedding_model)
        await store.delete_by_filter(kb.collection_name, f'document_id == "{str(doc.id)}"')
    except Exception as e:
        logger.warning("Failed to clean old vectors during retry", doc_id=str(doc.id), error=str(e))

    # Decrement KB counters atomically (old counts will be re-added after re-processing)
    await session.execute(
        sa_update(KnowledgeBase)
        .where(KnowledgeBase.id == kb.id)
        .values(
            doc_count=func.greatest(KnowledgeBase.doc_count - 1, 0),
            chunk_count=func.greatest(KnowledgeBase.chunk_count - old_chunk_count, 0),
        )
    )

    # Read file from disk and reset status
    content = Path(doc.storage_path).read_bytes()
    doc.status = "processing"
    doc.error_message = None
    doc.processing_progress = {"stage": "queued", "percent": 0, "message": "重新处理中..."}
    doc.chunk_count = 0
    await session.flush()
    await session.refresh(doc)

    # Commit BEFORE launching the task
    await session.commit()

    task = asyncio.create_task(
        _process_document(
            doc.id,
            kb.id,
            content,
            doc.filename,
            doc.mime_type or "text/plain",
            tenant_id=ctx.tenant_id,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return ApiResponse(data=_doc_to_out(doc))


@router.post("/documents/delete", response_model=ApiResponse, dependencies=[Depends(require_permission("knowledge.write"))])
async def delete_document(
    req: KBDocIdRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Delete a document, its chunks, and its vectors."""
    kb_id = uuid.UUID(req.kb_id)
    doc_id = uuid.UUID(req.id)
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    doc = await session.get(Document, doc_id)
    if not doc or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="Document not found")

    # 1. Remove vectors from Milvus
    try:
        from ai_platform.core.knowledge.store.milvus_store import get_milvus_store
        store = await get_milvus_store(kb.collection_name, kb.embedding_model)
        await store.delete_by_filter(kb.collection_name, f'document_id == "{str(doc.id)}"')
    except Exception as e:
        logger.warning("Milvus delete failed", doc_id=str(doc.id), error=str(e))

    # 2. Compute chunk count to update KB stats
    removed_chunks = doc.chunk_count or 0

    # 3. Delete PG chunk records
    chunks_result = await session.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
    )
    for chunk in chunks_result.scalars().all():
        await session.delete(chunk)

    # 4. Delete document record
    await session.delete(doc)

    # 5. Update KB counts atomically
    await session.execute(
        sa_update(KnowledgeBase)
        .where(KnowledgeBase.id == kb.id)
        .values(
            doc_count=func.greatest(KnowledgeBase.doc_count - 1, 0),
            chunk_count=func.greatest(KnowledgeBase.chunk_count - removed_chunks, 0),
        )
    )

    # 6. Cleanup files
    await session.flush()
    _cleanup_document_files(doc)

    return ApiResponse(message="Document deleted")


# =============================================================================
# Knowledge Query (RAG)
# =============================================================================


@router.post("/query", response_model=ApiResponse, dependencies=[Depends(require_permission("knowledge.read"))])
async def query_knowledge_base(
    req: KBQueryBody,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """
    Query a knowledge base — retrieve relevant chunks and optionally generate an answer.
    """
    kb_id = uuid.UUID(req.kb_id)
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    engine = KnowledgeEngine(session, tenant_id=ctx.tenant_id)
    chunks = await engine.query(
        req.question, [kb_id],
        top_k=req.top_k, score_threshold=req.score_threshold,
    )

    retrieved = [
        RetrievedChunkOut(
            content=c.content, score=c.score,
            document_id=str(c.document_id),
            source=c.metadata.get("filename"),
        )
        for c in chunks
    ]

    # Optionally generate an answer using LLM + retrieved context
    answer = None
    if req.generate_answer and chunks:
        context_text = "\n\n---\n\n".join(
            f"[Source {i+1}]: {c.content}" for i, c in enumerate(chunks)
        )
        system_prompt = (
            "You are a helpful assistant. Answer the user's question based ONLY on the "
            "provided context. If the context doesn't contain relevant information, say so clearly. "
            "Cite sources using [Source N] format."
        )
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=f"Context:\n{context_text}\n\nQuestion: {req.question}"),
        ]
        chat_request = ChatCompletionRequest(
            model=req.model, messages=messages, temperature=0.3,
        )
        llm = get_llm_client()
        response = await llm.chat(chat_request)
        if response.choices:
            answer = response.choices[0].message.content

    return ApiResponse(data={
        "answer": answer,
        "sources": retrieved,
        "chunks_count": len(chunks),
    })
