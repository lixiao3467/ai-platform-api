"""AI Platform Python SDK — one-line integration for business systems."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx


# =============================================================================
# Configuration
# =============================================================================


class AIPlatformConfig:
    """SDK configuration."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        jwt_token: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.jwt_token = jwt_token
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        elif self.jwt_token:
            h["Authorization"] = f"Bearer {self.jwt_token}"
        return h


# =============================================================================
# Async Client
# =============================================================================


class AIPlatformAsyncClient:
    """
    Async client for AI Platform API.

    Usage:
        client = AIPlatformAsyncClient(
            base_url="http://ai-platform.company.com",
            api_key="aiplat_xxxx",
        )

        # Simple chat
        response = await client.chat("你好", model="qwen-max")
        print(response["answer"])

        # Streaming chat
        async for chunk in client.chat_stream("写一首诗"):
            print(chunk, end="", flush=True)

        # Knowledge Q&A
        answer = await client.knowledge_query("kb-id-123", "年假怎么算？")

        # Agent execution
        result = await client.agent_run("agent-id-456", "分析上个月销售数据")
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        jwt_token: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._config = AIPlatformConfig(base_url, api_key, jwt_token, timeout)
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url,
            headers=self._config.headers,
            timeout=timeout,
        )

    async def __aenter__(self) -> AIPlatformAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # --- Chat ---

    async def chat(
        self,
        message: str,
        *,
        model: str = "qwen-max",
        system_prompt: str | None = None,
        conversation_id: str | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        """Send a chat message and get a complete response."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        if conversation_id:
            body["conversation_id"] = conversation_id

        resp = await self._client.post("/api/v1/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()

        return {
            "answer": data.get("choices", [{}])[0].get("message", {}).get("content", ""),
            "conversation_id": data.get("conversation_id"),
            "model": data.get("model"),
            "usage": data.get("usage"),
        }

    async def chat_stream(
        self,
        message: str,
        *,
        model: str = "qwen-max",
        system_prompt: str | None = None,
        conversation_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Send a chat message and stream the response token by token."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if conversation_id:
            body["conversation_id"] = conversation_id

        async with self._client.stream(
            "POST", "/api/v1/chat/completions", json=body
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

    # --- Knowledge Base ---

    async def knowledge_query(
        self,
        kb_id: str,
        question: str,
        *,
        top_k: int = 5,
        generate_answer: bool = True,
        model: str = "qwen-max",
    ) -> dict[str, Any]:
        """Query a knowledge base for answers."""
        resp = await self._client.post(
            f"/api/v1/knowledge-bases/{kb_id}/query",
            json={
                "question": question,
                "top_k": top_k,
                "generate_answer": generate_answer,
                "model": model,
            },
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def upload_document(
        self,
        kb_id: str,
        file_path: str,
        *,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Upload a document to a knowledge base."""
        import os

        fname = filename or os.path.basename(file_path)
        with open(file_path, "rb") as f:
            resp = await self._client.post(
                f"/api/v1/knowledge-bases/{kb_id}/documents",
                files={"file": (fname, f)},
                headers={"X-API-Key": self._config.api_key or ""},
            )
        resp.raise_for_status()
        return resp.json().get("data", {})

    # --- Agent ---

    async def agent_run(
        self,
        agent_id: str,
        input_text: str,
        *,
        stream: bool = False,
        context: dict | None = None,
    ) -> dict[str, Any]:
        """Run an agent."""
        body: dict[str, Any] = {"input": input_text, "stream": stream}
        if context:
            body["context"] = context

        resp = await self._client.post(
            f"/api/v1/agents/{agent_id}/run", json=body
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def agent_run_stream(
        self,
        agent_id: str,
        input_text: str,
        *,
        context: dict | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run an agent with streaming events."""
        body: dict[str, Any] = {"input": input_text, "stream": True}
        if context:
            body["context"] = context

        async with self._client.stream(
            "POST", f"/api/v1/agents/{agent_id}/run", json=body
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    payload = line[6:]
                    try:
                        event = json.loads(payload)
                        yield event
                    except json.JSONDecodeError:
                        continue

    # --- Workflow ---

    async def workflow_execute(
        self,
        workflow_id: str,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a workflow."""
        resp = await self._client.post(
            f"/api/v1/workflows/{workflow_id}/execute",
            json={"inputs": inputs or {}},
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    # --- Prompt ---

    async def prompt_render(
        self,
        prompt_id: str,
        variables: dict[str, Any],
        *,
        version: int | None = None,
    ) -> str:
        """Render a prompt template."""
        body: dict[str, Any] = {"variables": variables}
        if version:
            body["version"] = version

        resp = await self._client.post(
            f"/api/v1/prompts/{prompt_id}/render", json=body
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("rendered", "")

    # --- Health ---

    async def health(self) -> dict[str, Any]:
        """Check platform health."""
        resp = await self._client.get("/health")
        resp.raise_for_status()
        return resp.json()


# =============================================================================
# Sync Client (convenience wrapper)
# =============================================================================


class AIPlatformClient:
    """
    Synchronous client for AI Platform API.

    Usage:
        client = AIPlatformClient(
            base_url="http://ai-platform.company.com",
            api_key="aiplat_xxxx",
        )
        response = client.chat("你好")
        print(response["answer"])
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        jwt_token: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._config = AIPlatformConfig(base_url, api_key, jwt_token, timeout)
        self._client = httpx.Client(
            base_url=self._config.base_url,
            headers=self._config.headers,
            timeout=timeout,
        )

    def __enter__(self) -> AIPlatformClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def chat(
        self,
        message: str,
        *,
        model: str = "qwen-max",
        system_prompt: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a chat message."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if conversation_id:
            body["conversation_id"] = conversation_id

        resp = self._client.post("/api/v1/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()

        return {
            "answer": data.get("choices", [{}])[0].get("message", {}).get("content", ""),
            "conversation_id": data.get("conversation_id"),
            "model": data.get("model"),
            "usage": data.get("usage"),
        }

    def knowledge_query(
        self,
        kb_id: str,
        question: str,
        *,
        model: str = "qwen-max",
    ) -> dict[str, Any]:
        """Query a knowledge base."""
        resp = self._client.post(
            f"/api/v1/knowledge-bases/{kb_id}/query",
            json={"question": question, "generate_answer": True, "model": model},
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def agent_run(self, agent_id: str, input_text: str) -> dict[str, Any]:
        """Run an agent."""
        resp = self._client.post(
            f"/api/v1/agents/{agent_id}/run",
            json={"input": input_text, "stream": False},
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def health(self) -> dict[str, Any]:
        """Check health."""
        resp = self._client.get("/health")
        resp.raise_for_status()
        return resp.json()
