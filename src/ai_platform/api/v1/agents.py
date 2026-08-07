"""Agents API — /api/v1/agents/*."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.api.v1._shared import IdRequest
from ai_platform.core.agent.runtime import AgentConfig, AgentRuntime
from ai_platform.core.agent.tools.registry import get_tool_registry
from ai_platform.domain.models import Agent
from ai_platform.infra.database.connection import get_db

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class AgentCreateRequest(BaseModel):
    name: str = Field(max_length=128)
    description: str | None = None
    system_prompt: str = Field(default="You are a helpful AI assistant with access to tools.")
    model: str = Field(default="gpt-4o")
    tools: list[str] = Field(default_factory=lambda: ["http_request", "knowledge_search"])
    max_steps: int = Field(default=10, ge=1, le=50)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    model: str
    tools: list[str]
    max_steps: int
    status: str
    created_at: str


class AgentListRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AgentRunBody(BaseModel):
    id: str
    input: str = Field(description="User input for the agent")
    stream: bool = Field(default=False)
    context: dict | None = Field(default=None, description="Additional context")


# =============================================================================
# Tool Management — MUST be declared BEFORE id-based routes
# =============================================================================


@router.post("/tools/list", response_model=ApiResponse[list[dict]], dependencies=[Depends(require_permission("agent.read"))])
async def list_tools(
    ctx: RequestContext = Depends(get_request_context),
):
    """List all available tools."""
    registry = get_tool_registry()
    tools = registry.list_tools()
    return ApiResponse(data=[
        {
            "name": t.name,
            "description": t.description,
            "category": t.category,
            "parameters": t.parameters,
        }
        for t in tools
    ])


# =============================================================================
# Agent CRUD
# =============================================================================


@router.post("/create", response_model=ApiResponse[AgentOut], dependencies=[Depends(require_permission("agent.write"))])
async def create_agent(
    req: AgentCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Create a new agent."""
    agent = Agent(
        id=uuid.uuid4(),
        app_id=ctx.app_id or uuid.UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id=ctx.tenant_id,
        name=req.name,
        description=req.description,
        system_prompt=req.system_prompt,
        model=req.model,
        tools=req.tools,
        max_steps=req.max_steps,
        model_config_={"temperature": req.temperature},
    )
    session.add(agent)
    await session.flush()

    return ApiResponse(data=AgentOut(
        id=agent.id, name=agent.name, description=agent.description,
        model=agent.model, tools=agent.tools, max_steps=agent.max_steps,
        status=agent.status, created_at=agent.created_at.isoformat(),
    ))


@router.post("/list", response_model=ApiResponse[PaginatedResponse[AgentOut]], dependencies=[Depends(require_permission("agent.read"))])
async def list_agents(
    req: AgentListRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List agents for the current tenant."""
    page = req.page
    page_size = req.page_size
    offset = (page - 1) * page_size
    query = (
        select(Agent)
        .where(Agent.tenant_id == ctx.tenant_id)
        .order_by(Agent.created_at.desc())
        .offset(offset).limit(page_size)
    )
    result = await session.execute(query)
    agents = result.scalars().all()

    total = (await session.execute(
        select(func.count()).select_from(Agent).where(Agent.tenant_id == ctx.tenant_id)
    )).scalar() or 0

    items = [
        AgentOut(id=a.id, name=a.name, description=a.description,
                 model=a.model, tools=a.tools, max_steps=a.max_steps,
                 status=a.status, created_at=a.created_at.isoformat())
        for a in agents
    ]
    return ApiResponse(data=PaginatedResponse(items=items, total=total, page=page, page_size=page_size))


@router.post("/get", response_model=ApiResponse[AgentOut], dependencies=[Depends(require_permission("agent.read"))])
async def get_agent(
    req: IdRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get agent details."""
    agent_id = uuid.UUID(req.id)
    agent = await session.get(Agent, agent_id)
    if not agent or agent.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return ApiResponse(data=AgentOut(
        id=agent.id, name=agent.name, description=agent.description,
        model=agent.model, tools=agent.tools, max_steps=agent.max_steps,
        status=agent.status, created_at=agent.created_at.isoformat(),
    ))


@router.post("/delete", response_model=ApiResponse, dependencies=[Depends(require_permission("agent.write"))])
async def delete_agent(
    req: IdRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Delete an agent."""
    agent_id = uuid.UUID(req.id)
    agent = await session.get(Agent, agent_id)
    if not agent or agent.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    await session.delete(agent)
    return ApiResponse(message="Agent deleted")


# =============================================================================
# Agent Execution
# =============================================================================


@router.post("/run", dependencies=[Depends(require_permission("agent.execute"))])
async def run_agent(
    req: AgentRunBody,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """
    Execute an agent — run the ReAct loop with tools.

    Supports streaming (stream=true) for real-time event output.
    """
    agent_id = uuid.UUID(req.id)
    agent = await session.get(Agent, agent_id)
    if not agent or agent.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Agent not found")

    config = AgentConfig(
        name=agent.name,
        system_prompt=agent.system_prompt or "You are a helpful AI assistant.",
        model=agent.model,
        tools=agent.tools,
        max_steps=agent.max_steps,
        temperature=agent.model_config_.get("temperature", 0.7),
    )

    runtime = AgentRuntime()

    if req.stream:
        return StreamingResponse(
            runtime.run_stream(config, req.input, context=req.context),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = await runtime.run(config, req.input, context=req.context)
    return ApiResponse(data=result)
