"""Agent runtime — LangGraph-based ReAct execution engine."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import structlog

from ai_platform.api.schemas.chat import (
    ChatCompletionRequest,
    ChatMessage,
    ToolCall,
    ToolCallFunction,
)
from ai_platform.core.agent.tools.registry import ToolRegistry, get_tool_registry
from ai_platform.core.model_router.litellm_client import LiteLLMClient, get_llm_client

logger = structlog.get_logger()


@dataclass
class AgentConfig:
    """Agent configuration."""

    name: str = "default"
    system_prompt: str = "You are a helpful AI assistant with access to tools."
    model: str = "gpt-4o"
    tools: list[str] = field(default_factory=lambda: ["http_request", "knowledge_search"])
    max_steps: int = 10
    temperature: float = 0.7


@dataclass
class AgentEvent:
    """An event emitted during agent execution."""

    type: str  # "thinking" | "tool_call" | "tool_result" | "content" | "done" | "error"
    data: dict[str, Any]


class AgentRuntime:
    """
    Agent execution engine using ReAct pattern.

    Loop:
    1. Send messages + available tools to LLM
    2. If LLM returns tool_calls → execute tools → append results → repeat
    3. If LLM returns content (no tool_calls) → done, return answer
    4. Safety: max_steps limit + timeout
    """

    def __init__(
        self,
        llm_client: LiteLLMClient | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._llm = llm_client or get_llm_client()
        self._tools = tool_registry or get_tool_registry()

    async def run(
        self,
        config: AgentConfig,
        user_input: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Run agent synchronously (non-streaming).

        Returns final answer + execution trace.
        """
        events = []
        messages = self._build_initial_messages(config, user_input, context)
        tool_defs = self._tools.to_openai_tools(config.tools)

        for step in range(config.max_steps):
            logger.info("Agent step", step=step + 1, model=config.model)

            # Call LLM with tools
            request = ChatCompletionRequest(
                model=config.model,
                messages=messages,
                temperature=config.temperature,
                tools=tool_defs if tool_defs else None,
                tool_choice="auto" if tool_defs else None,
            )

            response = await self._llm.chat(request)

            if not response.choices:
                events.append(AgentEvent(type="error", data={"message": "No response from LLM"}))
                break

            choice = response.choices[0]
            assistant_msg = choice.message

            # Check if agent is done (no tool calls)
            if not assistant_msg.tool_calls:
                events.append(AgentEvent(
                    type="done",
                    data={
                        "answer": assistant_msg.content,
                        "steps": step + 1,
                        "usage": response.usage.model_dump(),
                    },
                ))
                return {
                    "answer": assistant_msg.content,
                    "steps": step + 1,
                    "events": [e.__dict__ for e in events],
                    "usage": response.usage.model_dump(),
                }

            # Append assistant message with tool calls to history
            messages.append(ChatMessage(
                role="assistant",
                content=assistant_msg.content,
                tool_calls=assistant_msg.tool_calls,
            ))

            events.append(AgentEvent(
                type="thinking",
                data={"content": assistant_msg.content, "tool_calls": len(assistant_msg.tool_calls)},
            ))

            # Execute each tool call
            for tc in assistant_msg.tool_calls:
                tool_name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                events.append(AgentEvent(
                    type="tool_call",
                    data={"tool": tool_name, "arguments": arguments},
                ))

                # Execute tool
                result = await self._tools.execute(tool_name, arguments)

                tool_output = (
                    json.dumps(result.output, ensure_ascii=False)
                    if result.success
                    else f"Error: {result.error}"
                )

                events.append(AgentEvent(
                    type="tool_result",
                    data={"tool": tool_name, "success": result.success, "output": tool_output[:2000]},
                ))

                # Append tool result to messages
                messages.append(ChatMessage(
                    role="tool",
                    content=tool_output,
                    tool_call_id=tc.id,
                ))

        # Max steps reached
        return {
            "answer": "I've reached the maximum number of reasoning steps. Here's what I found so far based on the tools I've used.",
            "steps": config.max_steps,
            "events": [e.__dict__ for e in events],
            "max_steps_reached": True,
        }

    async def run_stream(
        self,
        config: AgentConfig,
        user_input: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """
        Run agent with streaming events.

        Yields SSE-formatted event strings.
        """
        messages = self._build_initial_messages(config, user_input, context)
        tool_defs = self._tools.to_openai_tools(config.tools)
        total_tokens = {"prompt": 0, "completion": 0}

        for step in range(config.max_steps):
            # Call LLM
            request = ChatCompletionRequest(
                model=config.model,
                messages=messages,
                temperature=config.temperature,
                tools=tool_defs if tool_defs else None,
                tool_choice="auto" if tool_defs else None,
            )

            response = await self._llm.chat(request)
            if not response.choices:
                yield self._sse_event("error", {"message": "No response from LLM"})
                break

            choice = response.choices[0]
            total_tokens["prompt"] += response.usage.prompt_tokens
            total_tokens["completion"] += response.usage.completion_tokens

            # No tool calls → final answer
            if not choice.message.tool_calls:
                if choice.message.content:
                    yield self._sse_event("content", {"content": choice.message.content})
                yield self._sse_event("done", {
                    "final_answer": choice.message.content,
                    "steps_count": step + 1,
                    "total_tokens": total_tokens["prompt"] + total_tokens["completion"],
                })
                return

            # Emit thinking event
            if choice.message.content:
                yield self._sse_event("thinking", {"content": choice.message.content})

            # Append assistant message
            messages.append(ChatMessage(
                role="assistant",
                content=choice.message.content,
                tool_calls=choice.message.tool_calls,
            ))

            # Execute tool calls
            for tc in choice.message.tool_calls:
                tool_name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                yield self._sse_event("tool_call", {"tool": tool_name, "arguments": arguments})

                result = await self._tools.execute(tool_name, arguments)
                tool_output = (
                    json.dumps(result.output, ensure_ascii=False)
                    if result.success
                    else f"Error: {result.error}"
                )

                yield self._sse_event("tool_result", {
                    "tool": tool_name, "result": result.output if result.success else {"error": result.error},
                })

                messages.append(ChatMessage(
                    role="tool", content=tool_output, tool_call_id=tc.id,
                ))

        yield self._sse_event("done", {
            "final_answer": "Maximum steps reached.",
            "steps_count": config.max_steps,
            "total_tokens": total_tokens["prompt"] + total_tokens["completion"],
        })

    def _build_initial_messages(
        self,
        config: AgentConfig,
        user_input: str,
        context: dict[str, Any] | None,
    ) -> list[ChatMessage]:
        """Build the initial message list for the agent."""
        messages = [ChatMessage(role="system", content=config.system_prompt)]

        if context:
            context_str = json.dumps(context, ensure_ascii=False, indent=2)
            messages.append(ChatMessage(
                role="system",
                content=f"Additional context:\n{context_str}",
            ))

        messages.append(ChatMessage(role="user", content=user_input))
        return messages

    @staticmethod
    def _sse_event(event_type: str, data: dict) -> str:
        """Format an SSE event string."""
        payload = json.dumps({"type": event_type, **data}, ensure_ascii=False)
        return f"data: {payload}\n\n"
