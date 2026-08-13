"""Elasticsearch/OpenSearch hybrid search store for knowledge base chunks.

Uses opensearch-py (Bonsai runs OpenSearch, not Elasticsearch).
API is compatible with elasticsearch-py v7.x style.
"""

from __future__ import annotations

import structlog
from urllib.parse import urlparse

from ai_platform.config import get_settings

logger = structlog.get_logger()
_store_cache: dict[str, 'ElasticsearchStore'] = {}


class ElasticsearchStore:
    """OpenSearch store providing BM25 text + KNN vector hybrid search.

    Complements Milvus for hybrid retrieval with RRF (Reciprocal Rank Fusion).
    Uses opensearch-py — Bonsai runs OpenSearch 3.x, not Elasticsearch.
    """

    def __init__(self, index_name: str) -> None:
        self._index_name = index_name
        self._client: object | None = None
        self._available = True

    def _get_client(self):  # -> AsyncOpenSearch | None:
        """Lazy-init AsyncOpenSearch client with graceful degradation."""
        if self._client is not None:
            return self._client
        try:
            from opensearchpy import AsyncOpenSearch
        except ImportError:
            self._available = False
            logger.warning("opensearch-py package not installed, disabling ES")
            return None

        settings = get_settings()
        es_url = settings.elasticsearch_url
        logger.info("ES config read", url=es_url[:60] + "..." if len(es_url) > 60 else es_url)

        if not es_url or es_url == "http://localhost:9200":
            self._available = False
            logger.warning("ES URL not configured (or still default), disabling ES", raw_url=es_url[:80])
            return None

        try:
            parsed = urlparse(es_url)
            host = f"{parsed.hostname}:{parsed.port or 443}"
            auth = (parsed.username, parsed.password) if parsed.username else None
            self._client = AsyncOpenSearch(
                hosts=[{"host": parsed.hostname, "port": parsed.port or 443}],
                http_auth=auth,
                use_ssl=(parsed.scheme == "https"),
                verify_certs=False,
                ssl_show_warn=False,
            )
            logger.info("ES client created", host=parsed.hostname, port=parsed.port or 443)
        except Exception as exc:
            self._available = False
            logger.error("ES client creation FAILED", error=type(exc).__name__, detail=str(exc))
            return None

        return self._client

    def is_available(self) -> bool:
        return self._available

    async def ensure_index(self, dim: int = 1536) -> None:
        client = self._get_client()
        if not client:
            logger.warning("ES ensure_index skipped: no client", index=self._index_name)
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
                logger.info("ES index CREATED", index=self._index_name, dim=dim)
            else:
                logger.info("ES index already exists", index=self._index_name)
        except Exception as e:
            self._available = False
            logger.error(
                "ES ensure_index FAILED",
                index=self._index_name,
                error=type(e).__name__,
                detail=str(e),
            )

    async def index_chunk(
        self,
        chunk_id: str,
        content: str,
        embedding: list[float],
        metadata: dict,
    ) -> None:
        client = self._get_client()
        if not client:
            logger.warning("ES index_chunk skipped: no client", chunk_id=chunk_id)
            return
        try:
            await client.index(
                index=self._index_name,
                id=chunk_id,
                body={
                    "content": content,
                    "embedding": embedding,
                    "chunk_id": chunk_id,
                    **metadata,
                },
            )
        except Exception as e:
            logger.error(
                "ES index_chunk FAILED",
                chunk_id=chunk_id,
                error=type(e).__name__,
                detail=str(e),
            )

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
            hits = resp.get("hits", {}).get("hits", [])
            return [
                {"score": h["_score"], "metadata": h["_source"]} for h in hits
            ]
        except Exception as e:
            logger.error("ES search FAILED", error=type(e).__name__, detail=str(e))
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
