"""Embedding generation — abstracts embedding model providers."""

from __future__ import annotations

import structlog
from litellm import aembedding

from ai_platform.config import get_settings

logger = structlog.get_logger()


class Embedder:
    """Generate text embeddings via LiteLLM (unified interface)."""

    def __init__(self, model: str | None = None, dimensions: int | None = None) -> None:
        settings = get_settings()
        self._model = model or settings.embedding_model
        self._dimensions = dimensions or settings.embedding_dimensions
        self._api_base = settings.litellm_api_base
        self._api_key = settings.litellm_master_key

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


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """Get or create the embedder singleton."""
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
