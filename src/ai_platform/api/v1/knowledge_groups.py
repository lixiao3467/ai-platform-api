"""Knowledge Groups API — /api/v1/knowledge-groups/*."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse
from ai_platform.api.v1._shared import IdRequest
from ai_platform.domain.models import KnowledgeBase, KnowledgeGroup
from ai_platform.infra.database.connection import get_db

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class GroupCreateRequest(BaseModel):
    name: str = Field(..., max_length=128, min_length=1)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=64)
    parent_id: str | None = Field(default=None, description="UUID of parent group")
    sort_order: int = Field(default=0)


class GroupUpdateBody(BaseModel):
    id: str
    name: str | None = Field(default=None, max_length=128, min_length=1)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=64)
    parent_id: str | None = None
    sort_order: int | None = None


class GroupOut(BaseModel):
    id: str
    name: str
    description: str | None
    icon: str | None
    parent_id: str | None
    sort_order: int
    kb_count: int
    children: list[GroupOut] = []


# =============================================================================
# Helpers
# =============================================================================


async def _build_tree(
    groups: list[KnowledgeGroup],
    kb_counts: dict[uuid.UUID, int],
) -> list[GroupOut]:
    """Build a tree structure from a flat list of groups."""
    node_map: dict[uuid.UUID, GroupOut] = {}
    roots: list[GroupOut] = []

    # First pass: create all nodes
    for g in groups:
        node = GroupOut(
            id=str(g.id),
            name=g.name,
            description=g.description,
            icon=g.icon,
            parent_id=str(g.parent_id) if g.parent_id else None,
            sort_order=g.sort_order or 0,
            kb_count=kb_counts.get(g.id, 0),
        )
        node_map[g.id] = node

    # Second pass: wire up parent->children
    for g in groups:
        node = node_map[g.id]
        if g.parent_id and g.parent_id in node_map:
            node_map[g.parent_id].children.append(node)
        else:
            roots.append(node)

    # Sort each level by sort_order then name
    def _sort(nodes: list[GroupOut]) -> list[GroupOut]:
        nodes.sort(key=lambda n: (n.sort_order, n.name))
        for n in nodes:
            if n.children:
                _sort(n.children)
        return nodes

    return _sort(roots)


# =============================================================================
# Endpoints
# =============================================================================


@router.post(
    "/create",
    response_model=ApiResponse[GroupOut],
    status_code=201,
    summary="创建知识库分组",
    dependencies=[Depends(require_permission("knowledge.write"))],
)
async def create_group(
    req: GroupCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """创建知识库分组。"""
    # Validate parent_id if provided
    if req.parent_id:
        try:
            parent_uuid = uuid.UUID(req.parent_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="无效的 parent_id 格式")
        parent = await session.get(KnowledgeGroup, parent_uuid)
        if not parent or parent.tenant_id != ctx.tenant_id:
            raise HTTPException(status_code=404, detail="父分组不存在")

    # Check name uniqueness within tenant
    existing = await session.execute(
        select(KnowledgeGroup).where(
            KnowledgeGroup.tenant_id == ctx.tenant_id,
            KnowledgeGroup.name == req.name,
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"分组名已存在: {req.name}")

    group = KnowledgeGroup(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        parent_id=uuid.UUID(req.parent_id) if req.parent_id else None,
        name=req.name,
        description=req.description,
        icon=req.icon,
        sort_order=req.sort_order,
    )
    session.add(group)
    await session.flush()

    return ApiResponse(data=GroupOut(
        id=str(group.id),
        name=group.name,
        description=group.description,
        icon=group.icon,
        parent_id=str(group.parent_id) if group.parent_id else None,
        sort_order=group.sort_order or 0,
        kb_count=0,
    ))


@router.post(
    "/list",
    response_model=ApiResponse[list[GroupOut]],
    summary="知识库分组列表（树形）",
    dependencies=[Depends(require_permission("knowledge.read"))],
)
async def list_groups(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """获取当前租户的分组树。"""
    # Fetch all groups for tenant
    stmt = (
        select(KnowledgeGroup)
        .where(KnowledgeGroup.tenant_id == ctx.tenant_id)
        .order_by(KnowledgeGroup.sort_order, KnowledgeGroup.name)
    )
    result = await session.execute(stmt)
    groups = result.scalars().all()

    # Count KBs per group
    count_stmt = (
        select(KnowledgeBase.group_id, func.count())
        .where(KnowledgeBase.tenant_id == ctx.tenant_id, KnowledgeBase.group_id.isnot(None))
        .group_by(KnowledgeBase.group_id)
    )
    count_result = await session.execute(count_stmt)
    kb_counts: dict[uuid.UUID, int] = {row[0]: row[1] for row in count_result.all()}

    tree = await _build_tree(groups, kb_counts)
    return ApiResponse(data=tree)


@router.post(
    "/update",
    response_model=ApiResponse[GroupOut],
    summary="更新知识库分组",
    dependencies=[Depends(require_permission("knowledge.write"))],
)
async def update_group(
    req: GroupUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """更新分组信息。"""
    group_id = uuid.UUID(req.id)
    group = await session.get(KnowledgeGroup, group_id)
    if not group or group.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="分组不存在")

    if req.name is not None:
        # Check uniqueness (excluding self)
        dup = await session.execute(
            select(KnowledgeGroup).where(
                KnowledgeGroup.tenant_id == ctx.tenant_id,
                KnowledgeGroup.name == req.name,
                KnowledgeGroup.id != group_id,
            )
        )
        if dup.scalars().first():
            raise HTTPException(status_code=409, detail=f"分组名已存在: {req.name}")
        group.name = req.name

    if req.description is not None:
        group.description = req.description
    if req.icon is not None:
        group.icon = req.icon
    if req.sort_order is not None:
        group.sort_order = req.sort_order
    if req.parent_id is not None:
        if req.parent_id == "":
            group.parent_id = None
        else:
            try:
                parent_uuid = uuid.UUID(req.parent_id)
            except ValueError:
                raise HTTPException(status_code=422, detail="无效的 parent_id 格式")
            # Walk the ancestor chain to detect direct or indirect cycles (A→B→C→A)
            current_id = parent_uuid
            visited: set[uuid.UUID] = {group_id}
            while current_id is not None:
                if current_id in visited:
                    raise HTTPException(status_code=400, detail="会创建循环引用")
                visited.add(current_id)
                parent = await session.get(KnowledgeGroup, current_id)
                if not parent or parent.tenant_id != ctx.tenant_id:
                    raise HTTPException(status_code=404, detail="父分组不存在")
                current_id = parent.parent_id
            group.parent_id = parent_uuid

    await session.flush()

    # Re-fetch for clean output
    group = await session.get(KnowledgeGroup, group_id)

    # KB count
    count_stmt = (
        select(func.count())
        .select_from(KnowledgeBase)
        .where(KnowledgeBase.group_id == group_id, KnowledgeBase.tenant_id == ctx.tenant_id)
    )
    kb_count = (await session.execute(count_stmt)).scalar() or 0

    return ApiResponse(data=GroupOut(
        id=str(group.id),
        name=group.name,
        description=group.description,
        icon=group.icon,
        parent_id=str(group.parent_id) if group.parent_id else None,
        sort_order=group.sort_order or 0,
        kb_count=kb_count,
    ))


@router.post(
    "/delete",
    response_model=ApiResponse,
    summary="删除知识库分组",
    dependencies=[Depends(require_permission("knowledge.write"))],
)
async def delete_group(
    req: IdRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """删除分组。子分组的 parent_id 置空，KB 的 group_id 置空。"""
    group_id = uuid.UUID(req.id)
    group = await session.get(KnowledgeGroup, group_id)
    if not group or group.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="分组不存在")

    # Collect all descendant group ids (this group + recursive children)
    to_nullify: list[uuid.UUID] = [group_id]
    queue = [group_id]
    while queue:
        current = queue.pop(0)
        child_stmt = select(KnowledgeGroup.id).where(KnowledgeGroup.parent_id == current)
        child_result = await session.execute(child_stmt)
        child_ids = [row[0] for row in child_result.all()]
        to_nullify.extend(child_ids)
        queue.extend(child_ids)

    # Set KBs' group_id to null
    await session.execute(
        update(KnowledgeBase)
        .where(KnowledgeBase.group_id.in_(to_nullify))
        .values(group_id=None)
    )

    # Set child groups' parent_id to null (only direct children of the deleted group)
    await session.execute(
        update(KnowledgeGroup)
        .where(KnowledgeGroup.parent_id == group_id)
        .values(parent_id=None)
    )

    # Delete the group itself
    await session.delete(group)
    await session.flush()

    return ApiResponse(message="分组已删除")
