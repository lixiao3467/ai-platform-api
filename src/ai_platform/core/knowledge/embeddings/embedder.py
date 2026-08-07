"""Embedding generation — abstracts embedding model providers.

Supports two resolution paths:
1. **Environment config** (default / dev): uses ``settings.embedding_model``
   together with the LiteLLM proxy base URL / master key.
2. **Database config** (tenant-specific): when ``tenant_id`` and ``session``
   are provided, the embedder is resolved via
   :class:`ModelResolverService` with ``purpose="embedding"``.  This lets
   each tenant bring their own embedding model / API key.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from litellm import aembedding

from ai_platform.config import get_settings

logger = structlog.get_logger()


class Embedder:
    """Generate text embeddings via LiteLLM (unified interface)."""

    def __init__(
        self,
        model: str | None = None,
        dimensions: int | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        settings = get_settings()
        self._model = model or settings.embedding_model
        self._dimensions = dimensions or settings.embedding_dimensions
        self._api_base = api_base or settings.litellm_api_base
        self._api_key = api_key or settings.litellm_master_key

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        response = await aembedding(
            model=self._model,
            input=[text],
            api_base=self._api_base,
            api_key=self._api_key,
        )
        return response.data[0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts."""
        if not texts:
            return []

        # LiteLLM handles batching; split if needed for large batches
        all_embeddings = []
        batch_size = 100

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = await aembedding(
                model=self._model,
                input=batch,
                api_base=self._api_base,
                api_key=self._api_key,
            )
            all_embeddings.extend([item["embedding"] for item in response.data])

        return all_embeddings


async def create_embedder(
    tenant_id: uuid.UUID | None = None,
    session: Any | None = None,
) -> Embedder:
    """Create an Embedder, optionally resolved from tenant DB config.

    Resolution priority:
    1. If ``tenant_id`` + ``session`` provided: call
       ``ModelResolverService.get_default_for_purpose("embedding")``.
    2. If no DB match (or no session provided): fall back to
       ``settings.embedding_model`` + LiteLLM proxy credentials.
    """
    if tenant_id and session:
        try:
            from ai_platform.services.model_resolver import ModelResolverService

            resolver = ModelResolverService(session)
            config = await resolver.get_default_for_purpose(
                tenant_id, "embedding"
            )

            if config is not None:
                # Prefix provider name only when routing through LiteLLM proxy.
                # Direct API calls (e.g. DashScope) pass the model name as-is.
                model_name = config.model_name
                if (
                    "/" not in model_name
                    and config.provider_name
                    and config.provider_name != "env"
                ):
                    settings = get_settings()
                    if not config.api_base_url or config.api_base_url == settings.litellm_api_base:
                        model_name = f"{config.provider_name}/{model_name}"

                logger.info(
                    "Embedder resolved from DB",
                    tenant_id=str(tenant_id),
                    model=model_name,
                    provider=config.provider_name,
                )

                return Embedder(
                    model=model_name,
                    api_base=config.api_base_url,
                    api_key=config.api_key,
                )
        except Exception as exc:
            logger.warning(
                "DB embedder resolution failed, falling back to env",
                tenant_id=str(tenant_id),
                error=str(exc),
            )

    return Embedder()


# Backward-compat shim: environment-only singleton.
# Prefer ``create_embedder`` in new code.
_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """Get or create the env-configured embedder singleton.

    .. deprecated::
        Use :func:`create_embedder` for tenant-aware resolution.
    """
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
