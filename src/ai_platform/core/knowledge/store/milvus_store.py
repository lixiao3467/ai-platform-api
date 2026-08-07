"""Milvus vector store client."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    MilvusClient,
    connections,
)

from ai_platform.config import get_settings

logger = structlog.get_logger()


class MilvusStore:
    """Milvus vector store for knowledge base embeddings.

    The ``pymilvus`` client is blocking; every sync call is dispatched to a
    worker thread via ``asyncio.to_thread`` so the event loop is never blocked.
    """

    def __init__(self, collection_name: str, embedding_model: str, dim: int | None = None) -> None:
        settings = get_settings()
        self._uri = settings.milvus_uri
        self._token = settings.milvus_token
        self._collection_name = collection_name
        self._dim = dim
        self._client: MilvusClient | None = None

    async def _get_client(self) -> MilvusClient:
        """Get or create Milvus client."""
        if self._client is None:
            def _connect() -> MilvusClient:
                # Zilliz Cloud uses uri + token authentication
                if self._token:
                    return MilvusClient(uri=self._uri, token=self._token)
                return MilvusClient(uri=self._uri)

            self._client = await asyncio.to_thread(_connect)
            # Ensure collection exists
            has = await asyncio.to_thread(self._client.has_collection, self._collection_name)
            if not has:
                await self._create_collection_async()
        return self._client

    def _create_collection(self) -> None:
        """Create a Milvus collection with standard schema (synchronous)."""
        if self._client is None:
            return

        settings = get_settings()
        dim = self._dim or settings.embedding_dimensions

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=65535)
        schema.add_field(field_name="document_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="kb_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="chunk_index", datatype=DataType.INT64)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 128},
        )

        self._client.create_collection(
            collection_name=self._collection_name,
            schema=schema,
            index_params=index_params,
        )
        logger.info("Created Milvus collection", name=self._collection_name)

    async def _create_collection_async(self) -> None:
        """Async wrapper for _create_collection — runs in a worker thread."""
        await asyncio.to_thread(self._create_collection)

    async def insert(
        self,
        collection_name: str,
        vector_id: str,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> None:
        """Insert a vector with metadata."""
        client = await self._get_client()

        data = {
            "id": vector_id,
            "embedding": embedding,
            "content": metadata.get("content", "")[:65535],
            "document_id": metadata.get("document_id", ""),
            "kb_id": metadata.get("kb_id", ""),
            "chunk_index": metadata.get("chunk_index", 0),
        }

        await asyncio.to_thread(client.insert, collection_name=collection_name, data=[data])

    async def search(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search for similar vectors."""
        client = await self._get_client()

        def _search():
            return client.search(
                collection_name=collection_name,
                data=[query_embedding],
                limit=top_k,
                output_fields=["content", "document_id", "kb_id", "chunk_index"],
                search_params={"metric_type": "COSINE", "params": {"nprobe": 16}},
            )

        results = await asyncio.to_thread(_search)

        hits = []
        if results and results[0]:
            for hit in results[0]:
                hits.append({
                    "score": hit.get("distance", 0),
                    "metadata": {
                        "content": hit.get("entity", {}).get("content", ""),
                        "document_id": hit.get("entity", {}).get("document_id", ""),
                        "kb_id": hit.get("entity", {}).get("kb_id", ""),
                        "chunk_index": hit.get("entity", {}).get("chunk_index", 0),
                        "chunk_id": hit.get("id", ""),
                    },
                })

        return hits

    async def delete_collection(self, collection_name: str) -> None:
        """Drop a collection."""
        client = await self._get_client()
        has = await asyncio.to_thread(client.has_collection, collection_name)
        if has:
            await asyncio.to_thread(client.drop_collection, collection_name)
            logger.info("Dropped Milvus collection", name=collection_name)

    async def delete_by_filter(self, collection_name: str, filter_expr: str) -> int:
        """Delete vectors matching a filter expression. Returns the count deleted."""
        client = await self._get_client()
        has = await asyncio.to_thread(client.has_collection, collection_name)
        if not has:
            return 0

        def _delete():
            res = client.delete(collection_name=collection_name, filter=filter_expr)
            return res.get("delete_count", 0) if isinstance(res, dict) else 0

        count = await asyncio.to_thread(_delete)
        logger.info("Deleted vectors from Milvus", collection=collection_name, filter=filter_expr, count=count)
        return count


_store_cache: dict[str, MilvusStore] = {}


async def get_milvus_store(collection_name: str, embedding_model: str, dim: int | None = None) -> MilvusStore:
    """Get or create a MilvusStore instance (cached by collection name)."""
    if collection_name not in _store_cache:
        _store_cache[collection_name] = MilvusStore(collection_name, embedding_model, dim=dim)
    return _store_cache[collection_name]
