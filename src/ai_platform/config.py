"""
Configuration management — multi-source with Nacos integration.

Architecture:
    Priority (high → low):
    1. Environment variables (deployment overrides, K8s ConfigMap, etc.)
    2. Nacos config center (remote, hot-reload, versioned)
    3. .env file (local development fallback)

Nacos config structure:
    Namespace: dev / staging / production
    Group:     AI_PLATFORM
    DataIDs:
        - ai-platform.yaml        # Main application config
        - ai-platform-db.yaml     # Database connection pool etc.
        - ai-platform-redis.yaml  # Redis config
        - ai-platform-auth.yaml   # JWT/Auth config

Usage:
    from ai_platform.config import get_settings
    settings = get_settings()

    # Hot-reload callback
    from ai_platform.config import on_config_change
    @on_config_change("ai-platform.yaml")
    def handle_change(new_config):
        print("Config updated")
"""

from __future__ import annotations

import os
import json
import hashlib
import logging
from functools import lru_cache
from typing import Any, Callable

import yaml
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


# =============================================================================
# Nacos Client
# =============================================================================


class NacosClient:
    """
    Nacos configuration client.

    Features:
    - Multi-DataID config aggregation
    - Long-polling for config changes (hot-reload)
    - Graceful fallback when Nacos is unavailable
    - Deep merge of multiple config files
    """

    def __init__(
        self,
        server_addr: str,
        namespace: str = "",
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._server_addr = server_addr
        self._namespace = namespace
        self._username = username
        self._password = password
        self._client = None
        self._available = False
        self._cache: dict[str, dict] = {}
        self._listeners: dict[str, list[Callable]] = {}

    @property
    def available(self) -> bool:
        return self._available

    def connect(self) -> bool:
        """Connect to Nacos. Returns True if successful."""
        try:
            import nacos

            self._client = nacos.NacosClient(
                self._server_addr,
                namespace=self._namespace,
                username=self._username,
                password=self._password,
            )
            self._client.get_server_status()
            self._available = True
            logger.info("Nacos connected: %s (namespace=%s)", self._server_addr, self._namespace)
            return True
        except ImportError:
            logger.info("nacos-sdk-python not installed, using local config only")
            return False
        except Exception as e:
            logger.warning("Nacos unavailable: %s", e)
            return False

    def get_config(self, data_id: str, group: str = "AI_PLATFORM") -> dict | None:
        """Fetch a single config DataID from Nacos (YAML or JSON)."""
        if not self._available:
            return None
        try:
            import yaml

            content = self._client.get_config(data_id, group)
            if not content:
                return None
            try:
                parsed = yaml.safe_load(content)
            except Exception:
                parsed = json.loads(content)
            if isinstance(parsed, dict):
                self._cache[data_id] = parsed
            return parsed
        except Exception as e:
            logger.warning("Nacos fetch failed for %s: %s", data_id, e)
            return self._cache.get(data_id)

    def get_all_configs(self, data_ids: list[str], group: str = "AI_PLATFORM") -> dict:
        """Fetch and deep-merge multiple DataIDs (later overrides earlier)."""
        merged: dict = {}
        for data_id in data_ids:
            cfg = self.get_config(data_id, group)
            if cfg and isinstance(cfg, dict):
                merged = self._deep_merge(merged, cfg)
        return merged

    def subscribe(self, data_id: str, group: str, callback: Callable) -> None:
        """Register a callback for config changes on a DataID."""
        if not self._available:
            return
        key = f"{group}/{data_id}"
        self._listeners.setdefault(key, []).append(callback)

        def _watcher(change: dict) -> None:
            try:
                import yaml
                content = change.get("content", "")
                new_cfg = yaml.safe_load(content) or json.loads(content)
            except Exception:
                new_cfg = {}
            self._cache[data_id] = new_cfg if isinstance(new_cfg, dict) else {}
            for cb in self._listeners.get(key, []):
                try:
                    cb(new_cfg)
                except Exception as e:
                    logger.error("Config change callback error: %s", e)

        try:
            self._client.add_config_watcher(data_id, group, _watcher)
        except Exception as e:
            logger.warning("Nacos subscribe failed for %s: %s", data_id, e)

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        result = base.copy()
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = NacosClient._deep_merge(result[k], v)
            else:
                result[k] = v
        return result


# =============================================================================
# Hot-reload decorator
# =============================================================================

_change_callbacks: dict[str, list[Callable]] = {}


def on_config_change(data_id: str) -> Callable:
    """Decorator: register a callback for Nacos config changes."""
    def decorator(func: Callable) -> Callable:
        _change_callbacks.setdefault(data_id, []).append(func)
        return func
    return decorator


# =============================================================================
# Settings model
# =============================================================================


class AppSettings(BaseSettings):
    """
    Application settings — loaded from env vars > Nacos > .env (priority order).
    Every field can be overridden via uppercase environment variable.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Nacos ---
    nacos_server_addr: str = ""
    nacos_namespace: str = ""
    nacos_username: str = ""
    nacos_password: str = ""
    nacos_group: str = "AI_PLATFORM"
    nacos_enabled: bool = True  # Auto-detect: if server_addr is set, try Nacos

    # --- Application ---
    app_name: str = "ai-platform"
    app_env: str = Field(default="development")
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_log_level: str = "INFO"
    app_secret_key: str = "change-me-in-production-use-a-random-64-char-string"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_platform"
    database_pool_size: int = 20
    database_max_overflow: int = 10

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"
    redis_password: str = ""

    # --- LiteLLM ---
    litellm_api_base: str = "http://localhost:4000"
    litellm_master_key: str = "sk-litellm-master-key"

    # --- Embedding ---
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # --- Milvus (Zilliz Cloud) ---
    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = ""

    # --- Elasticsearch ---
    elasticsearch_url: str = "http://localhost:9200"

    # --- MinIO ---
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "ai-platform"

    # --- Langfuse ---
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # --- Auth ---
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # --- Rate Limiting ---
    rate_limit_default: int = 1000
    rate_limit_window_seconds: int = 60

    @computed_field
    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


# =============================================================================
# Multi-source config loader
# =============================================================================
#
# Priority chain (highest → lowest):
#
#   1. Environment variables    ← K8s ConfigMap, deployment overrides
#   2. Nacos config center      ← Remote, hot-reload, versioned
#   3. Local YAML config files  ← Fallback when Nacos unavailable
#   4. .env file                ← Pydantic auto-loads (local dev)
#   5. Code defaults            ← AppSettings field defaults
#
# Degradation: if Nacos fails → try local YAML → try .env → use defaults
# =============================================================================

_nacos: NacosClient | None = None
_instance: AppSettings | None = None

NACOS_DATA_IDS = [
    "ai-platform.yaml",
    "ai-platform-db.yaml",
    "ai-platform-auth.yaml",
]

# Local config file search paths (checked in order)
LOCAL_CONFIG_DIRS = [
    "config",              # ./config/ relative to CWD
    "conf",                # ./conf/ alternative
    "/etc/ai-platform",    # system-wide (Linux)
]


def _load_local_yaml_files() -> dict:
    """
    Load config from local YAML files as Nacos fallback.

    Searches config/, conf/, /etc/ai-platform/ for ai-platform*.yaml
    Returns merged dict (later DataIDs override earlier ones).
    """
    merged: dict = {}
    found: list[str] = []

    for config_dir in LOCAL_CONFIG_DIRS:
        if not os.path.isdir(config_dir):
            continue
        for data_id in NACOS_DATA_IDS:
            path = os.path.join(config_dir, data_id)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                if isinstance(content, dict):
                    merged = NacosClient._deep_merge(merged, content)
                    found.append(path)
            except Exception as e:
                logger.warning("Local config load failed: %s — %s", path, e)

    if found:
        logger.info("Local config files loaded: %s", found)
    return merged


def _try_load_nacos(env_overrides: dict) -> dict:
    """Attempt to load config from Nacos. Returns {} on any failure."""
    global _nacos

    server = env_overrides.get("nacos_server_addr") or os.getenv("NACOS_SERVER_ADDR", "")
    if not server:
        logger.debug("Nacos not configured (NACOS_SERVER_ADDR empty)")
        return {}

    _nacos = NacosClient(
        server_addr=server,
        namespace=env_overrides.get("nacos_namespace") or os.getenv("NACOS_NAMESPACE", ""),
        username=env_overrides.get("nacos_username") or os.getenv("NACOS_USERNAME") or None,
        password=env_overrides.get("nacos_password") or os.getenv("NACOS_PASSWORD") or None,
    )

    if not _nacos.connect():
        logger.warning("Nacos connect failed → fallback to local config")
        return {}

    group = env_overrides.get("nacos_group") or os.getenv("NACOS_GROUP", "AI_PLATFORM")
    remote = _nacos.get_all_configs(NACOS_DATA_IDS, group)

    if not remote:
        logger.warning("Nacos returned empty config → fallback to local config")
        return {}

    # Register hot-reload listeners
    for data_id in NACOS_DATA_IDS:
        for cb in _change_callbacks.get(data_id, []):
            _nacos.subscribe(data_id, group, cb)

    logger.info("Nacos config loaded: %s", list(remote.keys()))
    return remote


def get_settings(force_reload: bool = False) -> AppSettings:
    """
    Get settings singleton.

    Loading priority (highest → lowest):
    1. Environment variables
    2. Nacos config center (auto-fallback on failure)
    3. Local YAML config files (config/*.yaml)
    4. .env file (Pydantic auto-loads)
    5. Code defaults (AppSettings field defaults)
    """
    global _instance

    if _instance and not force_reload:
        return _instance

    # Layer 1: Environment variable overrides (highest priority)
    env_overrides: dict[str, Any] = {}
    for field_name in AppSettings.model_fields:
        val = os.getenv(field_name.upper())
        if val is not None:
            env_overrides[field_name] = val

    # Layer 2: Try Nacos
    nacos_config = _try_load_nacos(env_overrides)

    # Layer 3: If Nacos failed → try local YAML files
    local_config: dict = {}
    if not nacos_config:
        local_config = _load_local_yaml_files()

    # Merge: local yaml ← nacos ← env vars (each layer overrides the previous)
    merged: dict[str, Any] = {}
    merged.update(local_config)    # Layer 3
    merged.update(nacos_config)    # Layer 2 (overrides local)
    merged.update(env_overrides)   # Layer 1 (overrides everything)

    # Build settings (Pydantic handles .env + defaults internally)
    _instance = AppSettings(**merged)

    # Audit: which config source is active
    if nacos_config:
        source = "nacos"
    elif local_config:
        source = "local_yaml"
    elif os.path.isfile(".env"):
        source = ".env"
    else:
        source = "defaults"

    cfg_hash = hashlib.md5(
        json.dumps(merged, sort_keys=True, default=str).encode()
    ).hexdigest()[:8]

    nacos_status = "on" if _nacos and _nacos.available else "off"
    logger.info(
        "Config loaded: env=%s source=%s nacos=%s overrides=%d hash=%s",
        _instance.app_env, source, nacos_status, len(merged), cfg_hash,
    )

    return _instance


def get_nacos_client() -> NacosClient | None:
    """Get the Nacos client instance (for manual subscriptions)."""
    return _nacos
