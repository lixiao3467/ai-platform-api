"""Model resolver — resolve models by purpose from DB configuration.

Centralizes model resolution so that chat, agent, knowledge embedding,
and workflows all pull their models from the same managed provider list
instead of being hardcoded or environment-driven.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ai_platform.config import get_settings
from ai_platform.domain.models import ModelProvider

logger = structlog.get_logger()


# =============================================================================
# Data classes
# =============================================================================


@dataclass
class ModelConfig:
    """Resolved model configuration (includes decrypted credentials)."""

    provider_id: uuid.UUID
    provider_name: str
    provider_display: str | None
    model_name: str
    api_base_url: str | None
    api_key: str | None  # Decrypted — for internal service use only
    purposes: list[str]
    context_length: int | None
    priority: int


@dataclass
class ModelListItem:
    """Model summary for listing (no credentials)."""

    id: str  # provider id
    provider_name: str
    provider_display: str | None
    model_name: str
    purposes: list[str]
    context_length: int | None
    priority: int
    enabled: bool


# =============================================================================
# Purpose constants (used for validation / documentation)
# =============================================================================

VALID_PURPOSES = frozenset(
    {"llm", "embedding", "vision", "multimodal", "general", "chat"}
)


# =============================================================================
# Service
# =============================================================================


class ModelResolverService:
    """Resolve models from DB by purpose.

    The service reads ``ModelProvider.models`` (a JSON array) and filters /
    sorts entries by the ``purposes`` and ``enabled`` fields that each entry
    may carry.  Missing ``enabled`` is treated as ``True`` (backwards
    compatible with pre-migration rows).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_all_enabled_providers(
        self, tenant_id: uuid.UUID
    ) -> list[ModelProvider]:
        """Fetch enabled providers: tenant-specific first, then global."""
        tenant_stmt = (
            select(ModelProvider)
            .where(
                ModelProvider.tenant_id == tenant_id,
                ModelProvider.is_enabled.is_(True),
            )
            .order_by(ModelProvider.priority.desc())
        )
        tenant_result = await self._db.execute(tenant_stmt)
        tenant_providers = list(tenant_result.scalars().all())

        global_stmt = (
            select(ModelProvider)
            .where(
                ModelProvider.tenant_id.is_(None),
                ModelProvider.is_enabled.is_(True),
            )
            .order_by(ModelProvider.priority.desc())
        )
        global_result = await self._db.execute(global_stmt)
        global_providers = list(global_result.scalars().all())

        return [*tenant_providers, *global_providers]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_default_for_purpose(
        self,
        tenant_id: uuid.UUID,
        purpose: str,
    ) -> ModelConfig | None:
        """Return the highest-priority model for ``purpose``.

        The returned ``api_key`` is decrypted — only expose this object to
        internal services (Embedder, LiteLLMClient), never to HTTP responses.

        Falls back to environment-based settings when no DB entry matches,
        so callers always get a working config in development.
        """
        providers = await self._get_all_enabled_providers(tenant_id)

        best: ModelConfig | None = None
        best_priority = -1

        for provider in providers:
            models_config = provider.models or []
            for model_cfg in models_config:
                # Skip explicitly disabled models
                if model_cfg.get("enabled") is False:
                    continue

                purposes = model_cfg.get("purposes", [])
                if purpose not in purposes:
                    continue

                # Only consider providers with higher priority than what we
                # already selected.  Within equal-priority providers, the
                # first match wins (tenant providers come first).
                priority = provider.priority
                if priority < best_priority:
                    continue

                api_key = (
                    self._decrypt(provider.api_key_ref)
                    if provider.api_key_ref
                    else None
                )

                best = ModelConfig(
                    provider_id=provider.id,
                    provider_name=provider.provider_name,
                    provider_display=provider.display_name,
                    model_name=model_cfg.get("name", "unknown"),
                    api_base_url=provider.api_base_url,
                    api_key=api_key,
                    purposes=purposes,
                    context_length=model_cfg.get("context_length"),
                    priority=priority,
                )
                best_priority = priority

        return best

    async def list_available(
        self,
        tenant_id: uuid.UUID,
        purpose: str | None = None,
    ) -> list[ModelListItem]:
        """List all available models (enabled + purpose-filtered).

        Returns one item per (provider, model) pair, sorted by priority DESC.
        """
        providers = await self._get_all_enabled_providers(tenant_id)

        items: list[ModelListItem] = []
        for provider in providers:
            models_config = provider.models or []
            for model_cfg in models_config:
                if model_cfg.get("enabled") is False:
                    continue

                purposes = model_cfg.get("purposes", [])
                if purpose and purpose not in purposes:
                    continue

                items.append(
                    ModelListItem(
                        id=str(provider.id),
                        provider_name=provider.provider_name,
                        provider_display=provider.display_name,
                        model_name=model_cfg.get("name", "unknown"),
                        purposes=purposes,
                        context_length=model_cfg.get("context_length"),
                        priority=provider.priority,
                        enabled=True,
                    )
                )

        items.sort(key=lambda x: x.priority, reverse=True)
        return items

    async def toggle_model(
        self,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        model_name: str,
    ) -> bool:
        """Toggle ``enabled`` for a single model inside a provider.

        Returns the new enabled state.

        Raises:
            ValueError: provider not found, not owned by tenant, or model
                not present in the provider's ``models`` array.
        """
        provider = await self._db.get(ModelProvider, provider_id)
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        # Tenant isolation: only allow the owning tenant (or global admin).
        if provider.tenant_id is not None and provider.tenant_id != tenant_id:
            raise ValueError(f"Provider {provider_id} not found")

        models_list: list[dict[str, Any]] = provider.models or []
        found = False
        new_enabled = True
        for model_cfg in models_list:
            if model_cfg.get("name") == model_name:
                current = model_cfg.get("enabled", True)
                new_enabled = not current
                model_cfg["enabled"] = new_enabled
                found = True
                break

        if not found:
            raise ValueError(
                f"Model '{model_name}' not found in provider {provider_id}"
            )

        provider.models = models_list
        flag_modified(provider, "models")  # Force SQLAlchemy to emit UPDATE for JSON column
        await self._db.flush()

        logger.info(
            "Model toggled",
            provider_id=str(provider_id),
            model=model_name,
            enabled=new_enabled,
        )
        return new_enabled

    async def set_model_enabled(
        self,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        model_name: str,
        enabled: bool,
    ) -> bool:
        """Set ``enabled`` for a single model inside a provider.

        Returns the new enabled state.

        Raises:
            ValueError: provider not found, not owned by tenant, or model
                not present in the provider's ``models`` array.
        """
        provider = await self._db.get(ModelProvider, provider_id)
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        # Tenant isolation: only allow the owning tenant (or global admin).
        if provider.tenant_id is not None and provider.tenant_id != tenant_id:
            raise ValueError(f"Provider {provider_id} not found")

        models_list: list[dict[str, Any]] = provider.models or []
        found = False
        for model_cfg in models_list:
            if model_cfg.get("name") == model_name:
                model_cfg["enabled"] = enabled
                found = True
                break

        if not found:
            raise ValueError(
                f"Model '{model_name}' not found in provider {provider_id}"
            )

        provider.models = models_list
        flag_modified(provider, "models")  # Force SQLAlchemy to emit UPDATE for JSON column
        await self._db.flush()

        logger.info(
            "Model enabled state set",
            provider_id=str(provider_id),
            model=model_name,
            enabled=enabled,
        )
        return enabled

    async def get_decrypted_key(
        self, provider_id: uuid.UUID
    ) -> str | None:
        """Decrypt and return the API key for a provider (runtime use)."""
        provider = await self._db.get(ModelProvider, provider_id)
        if not provider or not provider.api_key_ref:
            return None
        return self._decrypt(provider.api_key_ref)

    # ------------------------------------------------------------------
    # Env-based fallback
    # ------------------------------------------------------------------

    @staticmethod
    def get_env_fallback(purpose: str) -> ModelConfig | None:
        """Build a ModelConfig from environment settings (dev fallback)."""
        settings = get_settings()

        if purpose == "embedding":
            return ModelConfig(
                provider_id=uuid.UUID(int=0),
                provider_name="env",
                provider_display="Environment Default",
                model_name=settings.embedding_model,
                api_base_url=settings.litellm_api_base,
                api_key=settings.litellm_master_key,
                purposes=["embedding"],
                context_length=None,
                priority=0,
            )
        if purpose in ("llm", "chat"):
            return ModelConfig(
                provider_id=uuid.UUID(int=0),
                provider_name="env",
                provider_display="Environment Default",
                model_name="gpt-4o",
                api_base_url=settings.litellm_api_base,
                api_key=settings.litellm_master_key,
                purposes=["llm", "chat"],
                context_length=128000,
                priority=0,
            )
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decrypt(encrypted: str) -> str | None:
        try:
            from ai_platform.infra.secrets.crypto import decrypt_secret

            return decrypt_secret(encrypted)
        except Exception as exc:
            logger.warning("Key decryption failed", error=str(exc))
            return None
