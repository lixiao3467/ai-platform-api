"""Elasticsearch hybrid search store for knowledge base chunks."""

from __future__ import annotations

import structlog
from urllib.parse import urlparse

from ai_platform.config import get_settings

logger = structlog.get_logger()
_store_cache: dict[str, 'ElasticsearchStore'] = {}


class ElasticsearchStore:
    """Elasticsearch store providing BM25 text + KNN vector hybrid search.

    Complements Milvus for hybrid retrieval with RRF (Reciprocal Rank Fusion).
    """

    def __init__(self, index_name: str) -> None:
        self._index_name = index_name
        self._client: object | None = None
        self._available = True

    def _get_client(self):  # -> AsyncElasticsearch | None:
        """Lazy-init AsyncElasticsearch client with graceful degradation."""
        if self._client is not None:
            return self._client
        try:
            from elasticsearch import AsyncElasticsearch
        except ImportError:
            self._available = False
            logger.warning("elasticsearch package not installed, disabling ES")
            return None
        settings = get_settings()
        es_url = settings.elasticsearch_url
        if not es_url or es_url == "http://localhost:9200":
            self._available = False
            logger.warning("ES URL not configured (or still default), disabling ES")
            return None
        try:
            parsed = urlparse(es_url)
            self._client = AsyncElasticsearch(
                hosts=[f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 443}"],
                http_auth=(parsed.username, parsed.password) if parsed.username else None,
                verify_certs=False,
            )
            logger.info(
                "ES client created",
                host=parsed.hostname,
                port=parsed.port or 443,
            )
        except Exception as exc:
            self._available = False
            logger.warning("ES client creation failed", error=str(exc))
            return None
        return self._client

    def is_available(self) -> bool:
        return self._available

    async def ensure_index(self, dim: int = 1536) -> None:
        client = self._get_client()
        if not client:
            return
        try:
            exists = await client.indices.exists(index=self._index_name)
            if not exists:
                await client.indices.create(
                    index=self._index_name,
                    body={
                        "mappings": {
                            "properties": {
                                "content": {"type": "text", "analyzer": "standard"},
                                "document_id": {"type": "keyword"},
                                "kb_id": {"type": "keyword"},
                                "chunk_id": {"type": "keyword"},
                                "chunk_index": {"type": "integer"},
                                "filename": {"type": "keyword"},
                                "embedding": {
                                    "type": "dense_vector",
                                    "dims": dim,
                                    "similarity": "cosine",
                                },
                            }
                        }
                    },
                )
                logger.info("ES index created", index=self._index_name)
        except Exception as e:
            self._available = False
            logger.warning("ES ensure_index failed", index=self._index_name, error=str(e))

    async def index_chunk(
        self,
        chunk_id: str,
        content: str,
        embedding: list[float],
        metadata: dict,
    ) -> None:
        client = self._get_client()
        if not client:
            return
        try:
            await client.index(
                index=self._index_name,
                id=chunk_id,
                body={
                    "content": content,
                    "embedding": embedding,
                    **metadata,
                },
            )
        except Exception as e:
            logger.warning("ES index_chunk failed", error=str(e))

    async def search(
        self,
        query_text: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict]:
        client = self._get_client()
        if not client:
            return []
        try:
            resp = await client.search(
                index=self._index_name,
                body={
                    "query": {
                        "multi_match": {
                            "query": query_text,
                            "fields": ["content"],
                            "type": "best_fields",
                        }
                    },
                    "size": top_k,
                },
            )
            hits = resp["hits"]["hits"]
            return [
                {"score": h["_score"], "metadata": h["_source"]} for h in hits
            ]
        except Exception as e:
            logger.warning("ES search failed", error=str(e))
            return []

    async def delete_index(self) -> None:
        client = self._get_client()
        if not client:
            return
        try:
            await client.indices.delete(index=self._index_name, ignore=[404])
        except Exception as e:
            logger.warning("ES delete_index failed", error=str(e))

    async def delete_by_document_id(self, doc_id: str) -> None:
        client = self._get_client()
        if not client:
            return
        try:
            await client.delete_by_query(
                index=self._index_name,
                body={"query": {"term": {"document_id": doc_id}}},
            )
        except Exception as e:
            logger.warning("ES delete_by_document_id failed", error=str(e))


async def get_es_store(kb_id: str) -> ElasticsearchStore | None:
    """Get or create a cached ElasticsearchStore instance for a knowledge base."""
    index_name = f"kb_{kb_id.replace('-', '_')}"
    if index_name not in _store_cache:
        _store_cache[index_name] = ElasticsearchStore(index_name)
    return _store_cache[index_name]