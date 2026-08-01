"""Workflow executor — traverses and executes the DAG."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.core.workflow.engine import (
    BaseNode,
    ConditionNode,
    ExecutionContext,
    ParallelNode,
    WorkflowDefinition,
    create_node,
)
from ai_platform.domain.enums import ExecutionStatus, NodeType, StepStatus
from ai_platform.domain.models import WorkflowExecution, WorkflowStep

logger = structlog.get_logger()


class WorkflowExecutor:
    """
    Executes a workflow DAG by traversing nodes in topological order.

    Features:
    - BFS execution following DAG edges
    - Parallel branch execution (asyncio.gather)
    - Conditional branching (evaluates edge conditions)
    - State persistence to PostgreSQL (each step recorded)
    - Retry on transient failures
    """

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def execute(
        self,
        workflow_def: WorkflowDefinition,
        inputs: dict[str, Any],
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
    ) -> WorkflowExecution:
        """
        Execute a workflow from START to END.

        1. Validate DAG
        2. Create execution record
        3. Traverse from START node via BFS
        4. Execute each node, persist step results
        5. Handle parallel branches, conditions, and human-in-loop pauses
        """
        # Validate
        errors = workflow_def.validate()
        if errors:
            raise ValueError(f"Invalid workflow: {'; '.join(errors)}")

        # Create execution record
        execution = WorkflowExecution(
            id=uuid.uuid4(),
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            inputs=inputs,
            status=ExecutionStatus.RUNNING,
        )
        self._db.add(execution)
        await self._db.flush()

        # Build context
        context = ExecutionContext(
            execution_id=str(execution.id),
            inputs=inputs,
            variables=workflow_def.variables,
        )

        # Find START node
        start_node = next(
            (n for n in workflow_def.nodes if n.type == NodeType.START), None
        )
        if not start_node:
            execution.status = ExecutionStatus.FAILED
            execution.error_message = "No START node found"
            await self._db.flush()
            return execution

        # BFS execution
        try:
            await self._execute_from_node(
                start_node.id, workflow_def, context, execution
            )
            execution.status = ExecutionStatus.COMPLETED
            execution.outputs = {
                "node_outputs": {
                    k: _safe_serialize(v) for k, v in context.node_outputs.items()
                }
            }
        except HumanApprovalRequired:
            execution.status = ExecutionStatus.PAUSED
            execution.current_node = context.node_outputs.get("_pending_node")
        except Exception as e:
            logger.error("Workflow execution failed", error=str(e), execution_id=str(execution.id))
            execution.status = ExecutionStatus.FAILED
            execution.error_message = str(e)[:1000]

        execution.completed_at = __import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        )
        await self._db.flush()
        return execution

    async def _execute_from_node(
        self,
        node_id: str,
        workflow_def: WorkflowDefinition,
        context: ExecutionContext,
        execution: WorkflowExecution,
    ) -> None:
        """Recursively execute from a given node."""
        node_def = workflow_def.get_node(node_id)
        if not node_def:
            return

        # Skip if already executed (parallel merge dedup)
        if node_id in context.node_outputs:
            return

        node = create_node(node_def)
        execution.current_node = node_id

        # Execute node
        step_start = time.time()
        try:
            output = await node.execute(context)
            context.set_node_output(node_id, output)
            context.node_statuses[node_id] = StepStatus.COMPLETED

            # Persist step
            await self._persist_step(
                execution.id, node_id, node_def.type.value,
                StepStatus.COMPLETED, output, step_start,
            )
        except HumanApprovalRequired:
            context.node_outputs["_pending_node"] = node_id
            raise
        except Exception as e:
            context.node_statuses[node_id] = StepStatus.FAILED
            await self._persist_step(
                execution.id, node_id, node_def.type.value,
                StepStatus.FAILED, None, step_start, error=str(e),
            )
            raise

        # Stop at END node
        if node_def.type == NodeType.END:
            return

        # Determine successors
        successors = workflow_def.get_successors(node_id)

        if not successors:
            return

        # Handle conditional branching
        if isinstance(node, ConditionNode):
            branch = output.get("branch", "default")
            conditional_edges = workflow_def.get_conditional_edges(node_id)
            matching = [
                e for e in conditional_edges
                if e.condition and branch in e.condition
            ]
            if matching:
                successors = [e.target for e in matching]
            else:
                # Default branch
                non_conditional = [
                    e.target for e in workflow_def.edges
                    if e.source == node_id and not e.condition
                ]
                successors = non_conditional if non_conditional else successors

        # Handle parallel execution
        if isinstance(node, ParallelNode):
            await asyncio.gather(*[
                self._execute_from_node(s, workflow_def, context, execution)
                for s in successors
            ])
        elif len(successors) > 1:
            # Multiple successors from non-parallel node — also run in parallel
            await asyncio.gather(*[
                self._execute_from_node(s, workflow_def, context, execution)
                for s in successors
            ])
        else:
            for s in successors:
                await self._execute_from_node(s, workflow_def, context, execution)

    async def _persist_step(
        self,
        execution_id: uuid.UUID,
        node_id: str,
        node_type: str,
        status: StepStatus,
        output: Any,
        start_time: float,
        error: str | None = None,
    ) -> None:
        """Persist a workflow step to the database."""
        import datetime

        step = WorkflowStep(
            id=uuid.uuid4(),
            execution_id=execution_id,
            node_id=node_id,
            node_type=node_type,
            status=status,
            outputs=_safe_serialize(output),
            error_message=error,
            started_at=datetime.datetime.fromtimestamp(start_time, tz=datetime.timezone.utc),
            completed_at=datetime.datetime.now(tz=datetime.timezone.utc),
            duration_ms=int((time.time() - start_time) * 1000),
        )
        self._db.add(step)
        await self._db.flush()


class HumanApprovalRequired(Exception):
    """Raised when a workflow pauses for human input."""

    pass


def _safe_serialize(obj: Any) -> Any:
    """Safely convert an object for JSON storage."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(item) for item in obj]
    return str(obj)
