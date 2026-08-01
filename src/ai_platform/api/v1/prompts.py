"""Prompts API — /api/v1/prompts/*."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.core.prompt.manager import PromptService
from ai_platform.domain.models import PromptTemplate, PromptVersion
from ai_platform.infra.database.connection import get_db

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class PromptCreateRequest(BaseModel):
    name: str = Field(max_length=128)
    content: str = Field(description="Jinja2 template content")
    description: str | None = None
    variables: list[dict] | None = None
    model_config: dict | None = None


class PromptVersionRequest(BaseModel):
    content: str
    change_note: str | None = None
    variables: list[dict] | None = None
    model_config: dict | None = None


class PromptRenderRequest(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None


class PromptABRequest(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)
    version_a: int
    version_b: int


class PromptOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    current_version: int
    created_at: str


class PromptVersionOut(BaseModel):
    version: int
    content: str
    variables: list | None
    change_note: str | None
    created_by: str | None
    created_at: str


# =============================================================================
# Template CRUD
# =============================================================================


@router.post("/", response_model=ApiResponse[PromptOut])
async def create_prompt(
    req: PromptCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Create a new prompt template with initial version."""
    svc = PromptService(session)
    try:
        template = await svc.create_template(
            ctx.tenant_id,
            name=req.name,
            content=req.content,
            description=req.description,
            variables=req.variables,
            model_config=req.model_config,
            app_id=ctx.app_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return ApiResponse(data=PromptOut(
        id=template.id, name=template.name, description=template.description,
        current_version=template.current_version,
        created_at=template.created_at.isoformat(),
    ))


@router.get("/", response_model=ApiResponse[PaginatedResponse[PromptOut]])
async def list_prompts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List prompt templates."""
    offset = (page - 1) * page_size
    query = (
        select(PromptTemplate)
        .where(PromptTemplate.tenant_id == ctx.tenant_id)
        .order_by(PromptTemplate.created_at.desc())
        .offset(offset).limit(page_size)
    )
    result = await session.execute(query)
    templates = result.scalars().all()

    total = (await session.execute(
        select(func.count()).select_from(PromptTemplate).where(
            PromptTemplate.tenant_id == ctx.tenant_id
        )
    )).scalar() or 0

    items = [
        PromptOut(
            id=t.id, name=t.name, description=t.description,
            current_version=t.current_version,
            created_at=t.created_at.isoformat(),
        )
        for t in templates
    ]
    return ApiResponse(data=PaginatedResponse(items=items, total=total, page=page, page_size=page_size))


@router.post("/{prompt_id}/versions", response_model=ApiResponse[PromptVersionOut])
async def create_version(
    prompt_id: uuid.UUID,
    req: PromptVersionRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Create a new version of a prompt template."""
    svc = PromptService(session)
    try:
        version = await svc.create_version(
            prompt_id, req.content,
            change_note=req.change_note,
            variables=req.variables,
            model_config=req.model_config,
            created_by=ctx.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return ApiResponse(data=PromptVersionOut(
        version=version.version, content=version.content,
        variables=version.variables, change_note=version.change_note,
        created_by=version.created_by,
        created_at=version.created_at.isoformat(),
    ))


@router.get("/{prompt_id}/versions", response_model=ApiResponse[list[PromptVersionOut]])
async def list_versions(
    prompt_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List all versions of a prompt template."""
    svc = PromptService(session)
    versions = await svc.get_versions(prompt_id)
    return ApiResponse(data=[
        PromptVersionOut(
            version=v.version, content=v.content,
            variables=v.variables, change_note=v.change_note,
            created_by=v.created_by,
            created_at=v.created_at.isoformat(),
        )
        for v in versions
    ])


@router.post("/{prompt_id}/render", response_model=ApiResponse[dict])
async def render_prompt(
    prompt_id: uuid.UUID,
    req: PromptRenderRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Render a prompt template with variables."""
    svc = PromptService(session)
    try:
        rendered = await svc.render(prompt_id, req.variables, version=req.version)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return ApiResponse(data={"rendered": rendered})


@router.post("/{prompt_id}/ab-test", response_model=ApiResponse[dict])
async def ab_test_prompt(
    prompt_id: uuid.UUID,
    req: PromptABRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Render two versions for A/B comparison."""
    svc = PromptService(session)
    try:
        results = await svc.render_ab(
            prompt_id, req.variables, req.version_a, req.version_b,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return ApiResponse(data=results)


@router.get("/{prompt_id}/diff", response_model=ApiResponse[dict])
async def diff_versions(
    prompt_id: uuid.UUID,
    v1: int = Query(...),
    v2: int = Query(...),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Compare two versions of a prompt template."""
    svc = PromptService(session)
    try:
        diff = await svc.diff_versions(prompt_id, v1, v2)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return ApiResponse(data=diff)
