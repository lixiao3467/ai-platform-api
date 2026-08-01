"""Knowledge Bases API — /api/v1/knowledge-bases/*."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.schemas.chat import ChatCompletionRequest, ChatMessage
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.config import get_settings
from ai_platform.core.knowledge.engine import KnowledgeEngine
from ai_platform.core.model_router.litellm_client import get_llm_client
from ai_platform.domain.models import Document, KnowledgeBase
from ai_platform.infra.database.connection import get_db

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class KBCreateRequest(BaseModel):
    name: str = Field(max_length=128)
    description: str | None = None
    embedding_model: str = Field(default="text-embedding-3-small")
    chunk_size: int = Field(default=512, ge=100, le=2000)
    chunk_overlap: int = Field(default=64, ge=0, le=500)


class KBOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    embedding_model: str
    doc_count: int
    chunk_count: int
    status: str
    created_at: str


class DocOut(BaseModel):
    id: uuid.UUID
    filename: str
    mime_type: str | None
    file_size: int | None
    chunk_count: int
    status: str
    error_message: str | None
    created_at: str


class KBQueryRequest(BaseModel):
    question: str
    top_k: int = Field(default=5, ge=1, le=20)
    score_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    generate_answer: bool = Field(default=True, description="Use LLM to generate answer from chunks")
    model: str = Field(default="gpt-4o")


class RetrievedChunkOut(BaseModel):
    content: str
    score: float
    document_id: str
    source: str | None = None


# =============================================================================
# Knowledge Base CRUD
# =============================================================================


@router.post("/", response_model=ApiResponse[KBOut])
async def create_knowledge_base(
    req: KBCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Create a new knowledge base."""
    collection_name = f"kb_{uuid.uuid4().hex[:12]}"

    kb = KnowledgeBase(
        id=uuid.uuid4(),
        app_id=ctx.app_id or uuid.UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id=ctx.tenant_id,
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
        status=kb.status, created_at=kb.created_at.isoformat(),
    ))


@router.get("/", response_model=ApiResponse[PaginatedResponse[KBOut]])
async def list_knowledge_bases(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List knowledge bases for the current tenant."""
    offset = (page - 1) * page_size
    query = (
        select(KnowledgeBase)
        .where(KnowledgeBase.tenant_id == ctx.tenant_id)
        .order_by(KnowledgeBase.created_at.desc())
        .offset(offset).limit(page_size)
    )
    result = await session.execute(query)
    kbs = result.scalars().all()

    total = (await session.execute(
        select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.tenant_id == ctx.tenant_id)
    )).scalar() or 0

    items = [
        KBOut(id=kb.id, name=kb.name, description=kb.description,
              embedding_model=kb.embedding_model, doc_count=kb.doc_count,
              chunk_count=kb.chunk_count, status=kb.status,
              created_at=kb.created_at.isoformat())
        for kb in kbs
    ]
    return ApiResponse(data=PaginatedResponse(items=items, total=total, page=page, page_size=page_size))


@router.get("/{kb_id}", response_model=ApiResponse[KBOut])
async def get_knowledge_base(
    kb_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get knowledge base details."""
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return ApiResponse(data=KBOut(
        id=kb.id, name=kb.name, description=kb.description,
        embedding_model=kb.embedding_model, doc_count=kb.doc_count,
        chunk_count=kb.chunk_count, status=kb.status,
        created_at=kb.created_at.isoformat(),
    ))


@router.delete("/{kb_id}", response_model=ApiResponse)
async def delete_knowledge_base(
    kb_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Delete a knowledge base and all its documents/chunks."""
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # TODO: Delete Milvus collection
    await session.delete(kb)
    return ApiResponse(message="Knowledge base deleted")


# =============================================================================
# Document Management
# =============================================================================


@router.post("/{kb_id}/documents", response_model=ApiResponse[DocOut])
async def upload_document(
    kb_id: uuid.UUID,
    file: UploadFile = File(...),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Upload and ingest a document into the knowledge base."""
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    content = await file.read()

    # Create document record
    doc = Document(
        id=uuid.uuid4(),
        kb_id=kb.id,
        filename=file.filename or "unknown",
        mime_type=file.content_type or "text/plain",
        file_size=len(content),
        status="processing",
    )
    session.add(doc)
    await session.flush()

    # Ingest document (parse → chunk → embed → store)
    engine = KnowledgeEngine(session)
    chunk_count = await engine.ingest_document(doc, content, kb)

    return ApiResponse(data=DocOut(
        id=doc.id, filename=doc.filename, mime_type=doc.mime_type,
        file_size=doc.file_size, chunk_count=chunk_count,
        status=doc.status, error_message=doc.error_message,
        created_at=doc.created_at.isoformat(),
    ))


@router.get("/{kb_id}/documents", response_model=ApiResponse[list[DocOut]])
async def list_documents(
    kb_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List documents in a knowledge base."""
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    stmt = select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at.desc())
    result = await session.execute(stmt)
    docs = result.scalars().all()

    items = [
        DocOut(id=d.id, filename=d.filename, mime_type=d.mime_type,
               file_size=d.file_size, chunk_count=d.chunk_count,
               status=d.status, error_message=d.error_message,
               created_at=d.created_at.isoformat())
        for d in docs
    ]
    return ApiResponse(data=items)


# =============================================================================
# Knowledge Query (RAG)
# =============================================================================


@router.post("/{kb_id}/query", response_model=ApiResponse)
async def query_knowledge_base(
    kb_id: uuid.UUID,
    req: KBQueryRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """
    Query a knowledge base — retrieve relevant chunks and optionally generate an answer.
    """
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb or kb.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    engine = KnowledgeEngine(session)
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
