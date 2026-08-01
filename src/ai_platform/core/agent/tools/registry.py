"""Tool registry — manages tools available to agents."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import httpx
import structlog

logger = structlog.get_logger()


@dataclass
class ToolDefinition:
    """A tool that can be used by agents."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[..., Awaitable[Any]] | None = None
    category: str = "custom"
    timeout: int = 30
    auth_required: bool = False


@dataclass
class ToolResult:
    """Result of a tool execution."""

    success: bool
    output: Any = None
    error: str | None = None


class ToolRegistry:
    """Registry for agent tools — manages registration, lookup, and execution."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._register_builtins()

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        logger.info("Tool registered", name=tool.name, category=tool.category)

    def get(self, name: str) -> ToolDefinition | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self, names: list[str] | None = None) -> list[ToolDefinition]:
        """List available tools, optionally filtered by name."""
        if names:
            return [t for n, t in self._tools.items() if n in names]
        return list(self._tools.values())

    def to_openai_tools(self, names: list[str] | None = None) -> list[dict]:
        """Convert tools to OpenAI function-calling format."""
        tools = self.list_tools(names)
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool by name with given arguments."""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(success=False, error=f"Unknown tool: {name}")

        if not tool.handler:
            return ToolResult(success=False, error=f"Tool '{name}' has no handler")

        try:
            logger.info("Executing tool", name=name, args_keys=list(arguments.keys()))
            result = await tool.handler(**arguments)
            return ToolResult(success=True, output=result)
        except Exception as e:
            logger.error("Tool execution failed", name=name, error=str(e))
            return ToolResult(success=False, error=str(e))

    def _register_builtins(self) -> None:
        """Register built-in tools."""

        # --- HTTP Request tool ---
        async def http_request(
            url: str,
            method: str = "GET",
            headers: dict | None = None,
            body: dict | None = None,
        ) -> dict:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    json=body if method.upper() in ("POST", "PUT", "PATCH") else None,
                )
                return {
                    "status_code": response.status_code,
                    "body": response.text[:5000],
                }

        self.register(ToolDefinition(
            name="http_request",
            description="Make HTTP requests to external APIs. Supports GET, POST, PUT, DELETE.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to request"},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE"], "default": "GET"},
                    "headers": {"type": "object", "description": "HTTP headers"},
                    "body": {"type": "object", "description": "Request body (for POST/PUT)"},
                },
                "required": ["url"],
            },
            handler=http_request,
            category="http_api",
        ))

        # --- Knowledge Search tool (placeholder) ---
        async def knowledge_search(query: str, kb_ids: list[str] | None = None, top_k: int = 3) -> dict:
            return {"message": "Knowledge search — wire up to KnowledgeEngine.query()", "query": query}

        self.register(ToolDefinition(
            name="knowledge_search",
            description="Search enterprise knowledge bases for relevant information.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "kb_ids": {"type": "array", "items": {"type": "string"}, "description": "Knowledge base IDs to search"},
                    "top_k": {"type": "integer", "default": 3},
                },
                "required": ["query"],
            },
            handler=knowledge_search,
            category="knowledge",
        ))

        # --- Python Execute tool (sandbox placeholder) ---
        async def python_execute(code: str) -> dict:
            # TODO: Implement proper sandbox execution
            return {"message": "Code execution sandbox — not yet implemented", "code": code[:200]}

        self.register(ToolDefinition(
            name="python_execute",
            description="Execute Python code in a sandboxed environment. Use for calculations, data processing.",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                },
                "required": ["code"],
            },
            handler=python_execute,
            category="code_exec",
        ))


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Get or create the tool registry singleton."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
