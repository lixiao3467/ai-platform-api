"""Workflow engine — DAG-based AI workflow orchestration."""

from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog
from jinja2 import Environment, meta, sandbox

from ai_platform.domain.enums import ExecutionStatus, NodeType, StepStatus

logger = structlog.get_logger()


# =============================================================================
# DAG Definition
# =============================================================================


@dataclass
class NodeDefinition:
    """A node in the workflow DAG."""

    id: str
    type: NodeType
    config: dict[str, Any] = field(default_factory=dict)
    position: dict[str, float] = field(default_factory=dict)  # for visual editor


@dataclass
class EdgeDefinition:
    """An edge connecting two nodes."""

    source: str
    target: str
    condition: str | None = None  # Jinja2 expression for conditional edges


@dataclass
class WorkflowDefinition:
    """Complete workflow DAG definition."""

    nodes: list[NodeDefinition]
    edges: list[EdgeDefinition]
    variables: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """Validate DAG structure. Returns list of errors (empty = valid)."""
        errors = []
        node_ids = {n.id for n in self.nodes}

        # Check for orphan edges
        for edge in self.edges:
            if edge.source not in node_ids:
                errors.append(f"Edge source '{edge.source}' not found in nodes")
            if edge.target not in node_ids:
                errors.append(f"Edge target '{edge.target}' not found in nodes")

        # Check for start and end nodes
        start_nodes = [n for n in self.nodes if n.type == NodeType.START]
        end_nodes = [n for n in self.nodes if n.type == NodeType.END]
        if not start_nodes:
            errors.append("Workflow must have exactly one START node")
        if len(start_nodes) > 1:
            errors.append("Workflow must have exactly one START node")
        if not end_nodes:
            errors.append("Workflow must have at least one END node")

        # Check for cycles (topological sort)
        try:
            self._topological_sort()
        except ValueError as e:
            errors.append(str(e))

        return errors

    def _topological_sort(self) -> list[str]:
        """Kahn's algorithm for topological sort. Raises ValueError if cycle detected."""
        in_degree: dict[str, int] = {n.id: 0 for n in self.nodes}
        adjacency: dict[str, list[str]] = {n.id: [] for n in self.nodes}

        for edge in self.edges:
            adjacency[edge.source].append(edge.target)
            in_degree[edge.target] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        sorted_ids = []

        while queue:
            node_id = queue.pop(0)
            sorted_ids.append(node_id)
            for neighbor in adjacency.get(node_id, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_ids) != len(self.nodes):
            raise ValueError("Workflow contains a cycle — DAG must be acyclic")

        return sorted_ids

    def get_successors(self, node_id: str) -> list[str]:
        """Get successor node IDs."""
        return [e.target for e in self.edges if e.source == node_id]

    def get_predecessors(self, node_id: str) -> list[str]:
        """Get predecessor node IDs."""
        return [e.source for e in self.edges if e.target == node_id]

    def get_node(self, node_id: str) -> NodeDefinition | None:
        """Get a node by ID."""
        return next((n for n in self.nodes if n.id == node_id), None)

    def get_conditional_edges(self, source_id: str) -> list[EdgeDefinition]:
        """Get edges from a source that have conditions."""
        return [e for e in self.edges if e.source == source_id and e.condition]


# =============================================================================
# Execution Context
# =============================================================================


class ExecutionContext:
    """
    Shared state container for a workflow execution.

    Supports Jinja2 variable resolution:
    - {{inputs.question}} → from workflow inputs
    - {{nodes.llm_call.output.content}} → from node outputs
    - {{vars.api_key}} → from global variables
    """

    def __init__(
        self,
        execution_id: str,
        inputs: dict[str, Any],
        variables: dict[str, Any] | None = None,
    ) -> None:
        self.execution_id = execution_id
        self.inputs = inputs
        self.variables = variables or {}
        self.node_outputs: dict[str, Any] = {}
        self.node_statuses: dict[str, StepStatus] = {}
        self._jinja_env = sandbox.SandboxedEnvironment()

    def set_node_output(self, node_id: str, output: Any) -> None:
        """Store a node's output."""
        self.node_outputs[node_id] = output

    def get_node_output(self, node_id: str) -> Any:
        """Retrieve a node's output."""
        return self.node_outputs.get(node_id)

    def resolve_expression(self, expression: str) -> Any:
        """
        Resolve a Jinja2 expression against the context.

        Examples:
            "{{inputs.question}}" → inputs["question"]
            "{{nodes.llm_1.output.answer}}" → node_outputs["llm_1"]["answer"]
            "{{vars.threshold > 0.5}}" → boolean evaluation
        """
        template = self._jinja_env.from_string(expression)
        context = {
            "inputs": self.inputs,
            "nodes": {
                nid: {"output": out} for nid, out in self.node_outputs.items()
            },
            "vars": self.variables,
        }
        try:
            return template.render(**context)
        except Exception as e:
            logger.warning("Expression resolution failed", expression=expression, error=str(e))
            return expression


# =============================================================================
# Base Node
# =============================================================================


class BaseNode(ABC):
    """Abstract base for all workflow node implementations."""

    node_type: NodeType

    def __init__(self, definition: NodeDefinition) -> None:
        self.definition = definition
        self.config = definition.config

    @abstractmethod
    async def execute(self, context: ExecutionContext) -> Any:
        """Execute the node. Returns output to store in context."""
        ...


# =============================================================================
# Node Implementations
# =============================================================================


class StartNode(BaseNode):
    node_type = NodeType.START

    async def execute(self, context: ExecutionContext) -> Any:
        return {"started": True, "execution_id": context.execution_id}


class EndNode(BaseNode):
    node_type = NodeType.END

    async def execute(self, context: ExecutionContext) -> Any:
        # Collect all node outputs as final result
        return {"completed": True, "all_outputs": context.node_outputs}


class LLMCallNode(BaseNode):
    node_type = NodeType.LLM_CALL

    async def execute(self, context: ExecutionContext) -> Any:
        from ai_platform.core.model_router.litellm_client import get_llm_client
        from ai_platform.api.schemas.chat import ChatCompletionRequest, ChatMessage

        model = self.config.get("model", "qwen-max")
        prompt_template = self.config.get("prompt", "{{inputs.question}}")
        system_prompt = self.config.get("system_prompt", "You are a helpful assistant.")

        # Resolve variables in prompt
        user_content = context.resolve_expression(prompt_template)

        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_content),
        ]

        llm = get_llm_client()
        request = ChatCompletionRequest(
            model=model,
            messages=messages,
            temperature=self.config.get("temperature", 0.7),
            max_tokens=self.config.get("max_tokens"),
        )

        response = await llm.chat(request)
        content = response.choices[0].message.content if response.choices else ""

        return {
            "content": content,
            "model": response.model,
            "usage": response.usage.model_dump() if response.usage else {},
        }


class RAGQueryNode(BaseNode):
    node_type = NodeType.RAG_QUERY

    async def execute(self, context: ExecutionContext) -> Any:
        query_template = self.config.get("query", "{{inputs.question}}")
        query = context.resolve_expression(query_template)
        kb_ids = self.config.get("kb_ids", [])
        top_k = self.config.get("top_k", 5)

        if not kb_ids:
            return {"chunks": [], "error": "No knowledge base IDs configured"}

        from ai_platform.core.knowledge.engine import KnowledgeEngine
        from ai_platform.infra.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            engine = KnowledgeEngine(session)
            chunks = await engine.query(query, [uuid.UUID(kb) for kb in kb_ids], top_k=top_k)

        return {
            "chunks": [{"content": c.content, "score": c.score} for c in chunks],
            "query": query,
            "count": len(chunks),
        }


class HTTPRequestNode(BaseNode):
    node_type = NodeType.HTTP_REQUEST

    async def execute(self, context: ExecutionContext) -> Any:
        import httpx

        url = context.resolve_expression(self.config.get("url", ""))
        method = self.config.get("method", "GET").upper()
        headers = self.config.get("headers", {})
        body = self.config.get("body")
        timeout = self.config.get("timeout", 30)

        # Resolve variables in headers and body
        if isinstance(body, dict):
            for key in body:
                if isinstance(body[key], str):
                    body[key] = context.resolve_expression(body[key])

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=body if method in ("POST", "PUT", "PATCH") else None,
            )

        return {
            "status_code": response.status_code,
            "body": response.text[:10000],
            "headers": dict(response.headers),
        }


class ConditionNode(BaseNode):
    node_type = NodeType.CONDITION

    async def execute(self, context: ExecutionContext) -> Any:
        """Evaluate conditions and return which branch to take."""
        conditions = self.config.get("conditions", [])

        for cond in conditions:
            expression = cond.get("expression", "false")
            branch = cond.get("branch", "default")
            try:
                result = context.resolve_expression("{{" + expression + "}}")
                if result and result.lower() not in ("false", "0", "none", ""):
                    return {"branch": branch, "expression": expression}
            except Exception:
                continue

        return {"branch": self.config.get("default_branch", "default")}


class ParallelNode(BaseNode):
    node_type = NodeType.PARALLEL

    async def execute(self, context: ExecutionContext) -> Any:
        """Marker node — actual parallelism handled by the executor."""
        return {"type": "parallel_gate", "mode": "fork"}


class MergeNode(BaseNode):
    node_type = NodeType.MERGE

    async def execute(self, context: ExecutionContext) -> Any:
        """Marker node — collects parallel branch outputs."""
        return {"type": "merge_gate"}


class DelayNode(BaseNode):
    node_type = NodeType.DELAY

    async def execute(self, context: ExecutionContext) -> Any:
        seconds = self.config.get("seconds", 1)
        await asyncio.sleep(seconds)
        return {"delayed": seconds}


class HumanInLoopNode(BaseNode):
    node_type = NodeType.HUMAN_IN_LOOP

    async def execute(self, context: ExecutionContext) -> Any:
        """Pause execution for human approval. Returns pending status."""
        return {
            "status": "pending_approval",
            "message": self.config.get("message", "Waiting for human approval"),
            "approval_data_schema": self.config.get("approval_schema"),
        }


class CodeExecNode(BaseNode):
    node_type = NodeType.CODE_EXEC

    async def execute(self, context: ExecutionContext) -> Any:
        """Execute sandboxed Python code (placeholder — needs proper sandbox)."""
        code = self.config.get("code", "")
        # TODO: Implement proper sandboxed execution (gVisor/Firecracker)
        return {"status": "not_implemented", "message": "Code execution sandbox pending"}


class SubWorkflowNode(BaseNode):
    node_type = NodeType.SUB_WORKFLOW

    async def execute(self, context: ExecutionContext) -> Any:
        """Trigger a sub-workflow execution (placeholder)."""
        sub_workflow_id = self.config.get("workflow_id")
        return {"status": "not_implemented", "sub_workflow_id": sub_workflow_id}


# =============================================================================
# Node Registry
# =============================================================================

_NODE_TYPES: dict[NodeType, type[BaseNode]] = {
    NodeType.START: StartNode,
    NodeType.END: EndNode,
    NodeType.LLM_CALL: LLMCallNode,
    NodeType.RAG_QUERY: RAGQueryNode,
    NodeType.HTTP_REQUEST: HTTPRequestNode,
    NodeType.CONDITION: ConditionNode,
    NodeType.PARALLEL: ParallelNode,
    NodeType.MERGE: MergeNode,
    NodeType.DELAY: DelayNode,
    NodeType.HUMAN_IN_LOOP: HumanInLoopNode,
    NodeType.CODE_EXEC: CodeExecNode,
    NodeType.SUB_WORKFLOW: SubWorkflowNode,
}


def create_node(definition: NodeDefinition) -> BaseNode:
    """Factory: create a node instance from its definition."""
    node_class = _NODE_TYPES.get(definition.type)
    if not node_class:
        raise ValueError(f"Unknown node type: {definition.type}")
    return node_class(definition)
