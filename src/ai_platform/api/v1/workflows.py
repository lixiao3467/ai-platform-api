"""Workflow API — /api/v1/workflows/*."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.core.workflow.engine import (
    EdgeDefinition,
    NodeDefinition,
    WorkflowDefinition,
)
from ai_platform.core.workflow.executor import WorkflowExecutor
from ai_platform.domain.enums import NodeType
from ai_platform.domain.models import Workflow, WorkflowExecution, WorkflowStep
from ai_platform.infra.database.connection import get_db

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class NodeSchema(BaseModel):
    id: str
    type: NodeType
    config: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] = Field(default_factory=dict)


class EdgeSchema(BaseModel):
    source: str
    target: str
    condition: str | None = None


class WorkflowCreateRequest(BaseModel):
    name: str = Field(max_length=128)
    description: str | None = None
    nodes: list[NodeSchema]
    edges: list[EdgeSchema]
    variables: dict[str, Any] = Field(default_factory=dict)


class WorkflowOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    version: int
    status: str
    node_count: int
    created_at: str


class WorkflowDetailOut(WorkflowOut):
    definition: dict[str, Any]
    variables: dict[str, Any]


class WorkflowExecuteRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)


class ExecutionOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    status: str
    current_node: str | None
    started_at: str
    completed_at: str | None
    error_message: str | None
    outputs: dict[str, Any] | None


class StepOut(BaseModel):
    id: uuid.UUID
    node_id: str
    node_type: str
    status: str
    outputs: dict[str, Any] | None
    error_message: str | None
    duration_ms: int | None
    started_at: str | None
    completed_at: str | None


# =============================================================================
# Workflow CRUD
# =============================================================================


@router.post("/", response_model=ApiResponse[WorkflowOut], dependencies=[Depends(require_permission("workflow.write"))])
async def create_workflow(
    req: WorkflowCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Create a new workflow with DAG definition."""
    # Validate DAG
    wf_def = WorkflowDefinition(
        nodes=[NodeDefinition(id=n.id, type=n.type, config=n.config) for n in req.nodes],
        edges=[EdgeDefinition(source=e.source, target=e.target, condition=e.condition) for e in req.edges],
    )
    errors = wf_def.validate()
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    workflow = Workflow(
        id=uuid.uuid4(),
        app_id=ctx.app_id or uuid.UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id=ctx.tenant_id,
        name=req.name,
        description=req.description,
        definition={
            "nodes": [n.model_dump() for n in req.nodes],
            "edges": [e.model_dump() for e in req.edges],
        },
        variables=req.variables,
        status="draft",
    )
    session.add(workflow)
    await session.flush()

    return ApiResponse(data=WorkflowOut(
        id=workflow.id, name=workflow.name, description=workflow.description,
        version=workflow.version, status=workflow.status,
        node_count=len(req.nodes), created_at=workflow.created_at.isoformat(),
    ))


@router.get("/", response_model=ApiResponse[PaginatedResponse[WorkflowOut]], dependencies=[Depends(require_permission("workflow.read"))])
async def list_workflows(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List workflows for the current tenant."""
    offset = (page - 1) * page_size
    query = (
        select(Workflow)
        .where(Workflow.tenant_id == ctx.tenant_id)
        .order_by(Workflow.created_at.desc())
        .offset(offset).limit(page_size)
    )
    result = await session.execute(query)
    workflows = result.scalars().all()

    total = (await session.execute(
        select(func.count()).select_from(Workflow).where(Workflow.tenant_id == ctx.tenant_id)
    )).scalar() or 0

    items = [
        WorkflowOut(
            id=w.id, name=w.name, description=w.description,
            version=w.version, status=w.status,
            node_count=len(w.definition.get("nodes", [])),
            created_at=w.created_at.isoformat(),
        )
        for w in workflows
    ]
    return ApiResponse(data=PaginatedResponse(items=items, total=total, page=page, page_size=page_size))


@router.get("/{workflow_id}", response_model=ApiResponse[WorkflowDetailOut], dependencies=[Depends(require_permission("workflow.read"))])
async def get_workflow(
    workflow_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get workflow details including full DAG definition."""
    w = await session.get(Workflow, workflow_id)
    if not w or w.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return ApiResponse(data=WorkflowDetailOut(
        id=w.id, name=w.name, description=w.description,
        version=w.version, status=w.status,
        node_count=len(w.definition.get("nodes", [])),
        created_at=w.created_at.isoformat(),
        definition=w.definition,
        variables=w.variables,
    ))


@router.post("/{workflow_id}/publish", response_model=ApiResponse, dependencies=[Depends(require_permission("workflow.write"))])
async def publish_workflow(
    workflow_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Publish a workflow (make it executable)."""
    w = await session.get(Workflow, workflow_id)
    if not w or w.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    w.status = "published"
    await session.flush()
    return ApiResponse(message="Workflow published")


# =============================================================================
# Execution
# =============================================================================


@router.post("/{workflow_id}/execute", response_model=ApiResponse[ExecutionOut], dependencies=[Depends(require_permission("workflow.write"))])
async def execute_workflow(
    workflow_id: uuid.UUID,
    req: WorkflowExecuteRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Execute a published workflow."""
    w = await session.get(Workflow, workflow_id)
    if not w or w.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if w.status != "published":
        raise HTTPException(status_code=400, detail="Workflow must be published before execution")

    # Reconstruct WorkflowDefinition
    nodes = [NodeDefinition(**n) for n in w.definition.get("nodes", [])]
    edges = [EdgeDefinition(**e) for e in w.definition.get("edges", [])]
    wf_def = WorkflowDefinition(nodes=nodes, edges=edges, variables=w.variables)

    executor = WorkflowExecutor(session)
    execution = await executor.execute(
        wf_def, req.inputs, tenant_id=ctx.tenant_id, workflow_id=w.id,
    )

    return ApiResponse(data=ExecutionOut(
        id=execution.id, workflow_id=execution.workflow_id,
        status=execution.status, current_node=execution.current_node,
        started_at=execution.started_at.isoformat() if execution.started_at else "",
        completed_at=execution.completed_at.isoformat() if execution.completed_at else None,
        error_message=execution.error_message,
        outputs=execution.outputs,
    ))


@router.get("/executions/{exec_id}", response_model=ApiResponse[ExecutionOut], dependencies=[Depends(require_permission("workflow.read"))])
async def get_execution(
    exec_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get execution status."""
    ex = await session.get(WorkflowExecution, exec_id)
    if not ex or ex.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Execution not found")
    return ApiResponse(data=ExecutionOut(
        id=ex.id, workflow_id=ex.workflow_id, status=ex.status,
        current_node=ex.current_node,
        started_at=ex.started_at.isoformat() if ex.started_at else "",
        completed_at=ex.completed_at.isoformat() if ex.completed_at else None,
        error_message=ex.error_message, outputs=ex.outputs,
    ))


@router.get("/executions/{exec_id}/steps", response_model=ApiResponse[list[StepOut]], dependencies=[Depends(require_permission("workflow.read"))])
async def get_execution_steps(
    exec_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get all steps for an execution."""
    ex = await session.get(WorkflowExecution, exec_id)
    if not ex or ex.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Execution not found")

    stmt = (
        select(WorkflowStep)
        .where(WorkflowStep.execution_id == exec_id)
        .order_by(WorkflowStep.started_at)
    )
    result = await session.execute(stmt)
    steps = result.scalars().all()

    return ApiResponse(data=[
        StepOut(
            id=s.id, node_id=s.node_id, node_type=s.node_type,
            status=s.status, outputs=s.outputs, error_message=s.error_message,
            duration_ms=s.duration_ms,
            started_at=s.started_at.isoformat() if s.started_at else None,
            completed_at=s.completed_at.isoformat() if s.completed_at else None,
        )
        for s in steps
    ])
