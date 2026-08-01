"""Provider service — manage LLM providers and their encrypted API keys."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.domain.models import ModelProvider
from ai_platform.infra.secrets.crypto import decrypt_secret, encrypt_secret, mask_secret

logger = structlog.get_logger()


class ProviderService:
    """
    Manages LLM provider configurations.

    API keys are encrypted before storage and decrypted only at runtime
    when the LiteLLM client needs to make a call.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def create_provider(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_name: str,
        display_name: str | None = None,
        api_base_url: str | None = None,
        api_key: str | None = None,
        models: list[dict[str, Any]] | None = None,
        priority: int = 0,
    ) -> ModelProvider:
        """Create a new provider with encrypted API key."""
        encrypted_key = encrypt_secret(api_key) if api_key else None

        provider = ModelProvider(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            provider_name=provider_name,
            display_name=display_name or provider_name,
            api_base_url=api_base_url,
            api_key_ref=encrypted_key,
            models=models or [],
            is_enabled=True,
            priority=priority,
        )
        self._db.add(provider)
        await self._db.flush()

        logger.info(
            "Provider created",
            provider=provider_name,
            tenant_id=str(tenant_id),
        )
        return provider

    async def update_api_key(self, provider_id: uuid.UUID, new_api_key: str) -> None:
        """Update a provider's API key (re-encrypts)."""
        provider = await self._db.get(ModelProvider, provider_id)
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        provider.api_key_ref = encrypt_secret(new_api_key)
        await self._db.flush()

        logger.info("Provider API key updated", provider_id=str(provider_id))

    async def get_decrypted_key(self, provider_id: uuid.UUID) -> str | None:
        """Get the decrypted API key for a provider (runtime use only)."""
        provider = await self._db.get(ModelProvider, provider_id)
        if not provider or not provider.api_key_ref:
            return None

        return decrypt_secret(provider.api_key_ref)

    async def get_provider_by_name(
        self, tenant_id: uuid.UUID, provider_name: str
    ) -> ModelProvider | None:
        """Look up a provider by name within a tenant."""
        stmt = select(ModelProvider).where(
            ModelProvider.tenant_id == tenant_id,
            ModelProvider.provider_name == provider_name,
            ModelProvider.is_enabled.is_(True),
        )
        result = await self._db.execute(stmt)
        return result.scalars().first()

    async def get_key_for_model(
        self, tenant_id: uuid.UUID, model_name: str
    ) -> tuple[str | None, str | None]:
        """
        Resolve the API key and base URL for a given model name.

        Searches all enabled providers for the tenant, matching model_name
        against each provider's models list.

        Returns: (api_key, api_base_url) or (None, None) if not found.
        """
        stmt = (
            select(ModelProvider)
            .where(
                ModelProvider.tenant_id == tenant_id,
                ModelProvider.is_enabled.is_(True),
            )
            .order_by(ModelProvider.priority.desc())
        )
        result = await self._db.execute(stmt)
        providers = result.scalars().all()

        # Also check global providers (tenant_id IS NULL)
        global_stmt = (
            select(ModelProvider)
            .where(
                ModelProvider.tenant_id.is_(None),
                ModelProvider.is_enabled.is_(True),
            )
            .order_by(ModelProvider.priority.desc())
        )
        global_result = await self._db.execute(global_stmt)
        global_providers = global_result.scalars().all()

        for provider in [*providers, *global_providers]:
            models_config = provider.models or []
            for model_cfg in models_config:
                if model_cfg.get("name") == model_name or model_cfg.get("id") == model_name:
                    api_key = decrypt_secret(provider.api_key_ref) if provider.api_key_ref else None
                    return api_key, provider.api_base_url

        return None, None

    async def list_providers(
        self, tenant_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """List all providers for a tenant (keys masked)."""
        stmt = (
            select(ModelProvider)
            .where(ModelProvider.tenant_id == tenant_id)
            .order_by(ModelProvider.priority.desc(), ModelProvider.created_at)
        )
        result = await self._db.execute(stmt)
        providers = result.scalars().all()

        items = []
        for p in providers:
            key_display = None
            if p.api_key_ref:
                try:
                    raw_key = decrypt_secret(p.api_key_ref)
                    key_display = mask_secret(raw_key)
                except Exception:
                    key_display = "****(解密失败)"

            items.append({
                "id": str(p.id),
                "provider_name": p.provider_name,
                "display_name": p.display_name,
                "api_base_url": p.api_base_url,
                "api_key_display": key_display,
                "models": p.models,
                "is_enabled": p.is_enabled,
                "priority": p.priority,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            })

        return items

    async def toggle_provider(self, provider_id: uuid.UUID, enabled: bool) -> None:
        """Enable or disable a provider."""
        provider = await self._db.get(ModelProvider, provider_id)
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        provider.is_enabled = enabled
        await self._db.flush()

    async def delete_provider(self, provider_id: uuid.UUID) -> None:
        """Delete a provider and its encrypted key."""
        provider = await self._db.get(ModelProvider, provider_id)
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        await self._db.delete(provider)
        logger.info("Provider deleted", provider_id=str(provider_id))
