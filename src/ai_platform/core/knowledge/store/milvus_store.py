"""Milvus vector store client."""

from __future__ import annotations

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
    """Milvus vector store for knowledge base embeddings."""

    def __init__(self, collection_name: str, embedding_model: str) -> None:
        settings = get_settings()
        self._uri = settings.milvus_uri
        self._token = settings.milvus_token
        self._collection_name = collection_name
        self._client: MilvusClient | None = None

    async def _get_client(self) -> MilvusClient:
        """Get or create Milvus client."""
        if self._client is None:
            # Zilliz Cloud uses uri + token authentication
            if self._token:
                self._client = MilvusClient(uri=self._uri, token=self._token)
            else:
                # Local Milvus without authentication
                self._client = MilvusClient(uri=self._uri)
            # Ensure collection exists
            if not self._client.has_collection(self._collection_name):
                self._create_collection()
        return self._client

    def _create_collection(self) -> None:
        """Create a Milvus collection with standard schema."""
        if self._client is None:
            return

        settings = get_settings()
        dim = settings.embedding_dimensions

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

        client.insert(collection_name=collection_name, data=[data])

    async def search(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search for similar vectors."""
        client = await self._get_client()

        results = client.search(
            collection_name=collection_name,
            data=[query_embedding],
            limit=top_k,
            output_fields=["content", "document_id", "kb_id", "chunk_index"],
            search_params={"metric_type": "COSINE", "params": {"nprobe": 16}},
        )

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
        if client.has_collection(collection_name):
            client.drop_collection(collection_name)
            logger.info("Dropped Milvus collection", name=collection_name)


_store_cache: dict[str, MilvusStore] = {}


async def get_milvus_store(collection_name: str, embedding_model: str) -> MilvusStore:
    """Get or create a MilvusStore instance (cached by collection name)."""
    if collection_name not in _store_cache:
        _store_cache[collection_name] = MilvusStore(collection_name, embedding_model)
    return _store_cache[collection_name]
