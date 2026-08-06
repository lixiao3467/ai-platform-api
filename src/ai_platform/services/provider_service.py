"""Provider service — manage LLM providers and their encrypted API keys."""

from __future__ import annotations

import asyncio
import time
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

    async def update_provider(
        self,
        provider_id: uuid.UUID,
        *,
        display_name: str | None = None,
        api_base_url: str | None = None,
        api_key: str | None = None,
        models: list[dict[str, Any]] | None = None,
        priority: int | None = None,
    ) -> tuple[ModelProvider, bool]:
        """Update provider metadata (display_name, base_url, models, priority).

        If *api_key* is provided it is encrypted and stored as ``api_key_ref``.

        When ``api_base_url`` or ``api_key`` actually changes the provider is
        automatically disabled (``is_enabled=False``) so that the caller must
        re-test connectivity before re-enabling it.  The boolean returned
        alongside the model indicates whether such a reset happened (useful
        for setting ``needs_retest`` in the API response).
        """
        provider = await self._db.get(ModelProvider, provider_id)
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        needs_retest = False

        if display_name is not None:
            provider.display_name = display_name
        if api_base_url is not None and api_base_url != provider.api_base_url:
            provider.api_base_url = api_base_url
            needs_retest = True
        if api_key is not None:
            # Always treat a supplied key as a change — we can't compare
            # against the encrypted value cheaply.
            provider.api_key_ref = encrypt_secret(api_key)
            needs_retest = True
        if models is not None:
            provider.models = models
        if priority is not None:
            provider.priority = priority

        if needs_retest:
            provider.is_enabled = False

        await self._db.flush()
        logger.info(
            "Provider updated",
            provider_id=str(provider_id),
            needs_retest=needs_retest,
        )
        return provider, needs_retest

    # -----------------------------------------------------------------
    # Connectivity test
    # -----------------------------------------------------------------

    async def test_provider(
        self,
        provider_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Test connectivity to a provider.

        Strategy:
        1. Try ``litellm.amodels()`` — lightweight, no token cost.
        2. Fall back to a minimal chat completion (``max_tokens=1``) using the
           first enabled model configured for the provider.

        Returns a dict with keys: ``success``, ``latency_ms``, ``model``,
        ``message``.
        """
        provider = await self._db.get(ModelProvider, provider_id)
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        api_key = decrypt_secret(provider.api_key_ref) if provider.api_key_ref else None
        api_base_url = provider.api_base_url

        if not api_key:
            return {
                "success": False,
                "latency_ms": 0,
                "model": "",
                "message": "Provider has no API key configured",
            }

        # Lazy import to avoid startup cost
        from ai_platform.core.model_router.litellm_client import _get_litellm

        litellm = _get_litellm()
        start = time.monotonic()

        # --- Strategy 1: amodels() -----------------------------------------
        try:
            models_resp = await asyncio.wait_for(
                litellm.amodels(api_key=api_key, api_base=api_base_url),
                timeout=10,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)

            # amodels() returns different shapes depending on provider;
            # try to extract a representative model name.
            model_name = ""
            if isinstance(models_resp, dict):
                data = models_resp.get("data", [])
                if data and isinstance(data, list):
                    model_name = data[0].get("id", "") if isinstance(data[0], dict) else str(data[0])
            elif isinstance(models_resp, list) and models_resp:
                first = models_resp[0]
                model_name = first.get("id", "") if isinstance(first, dict) else str(first)

            return {
                "success": True,
                "latency_ms": elapsed_ms,
                "model": model_name or "unknown",
                "message": "连接成功",
            }
        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {
                "success": False,
                "latency_ms": elapsed_ms,
                "model": "",
                "message": "Connection timed out (10s)",
            }
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "amodels() unavailable, falling back to chat test",
                provider_id=str(provider_id),
                error=str(exc),
            )

        # --- Strategy 2: minimal chat completion ---------------------------
        # Pick the first enabled model from the provider config.
        model_name = ""
        for cfg in (provider.models or []):
            if cfg.get("enabled") is not False:
                model_name = cfg.get("name") or cfg.get("id", "")
                break

        if not model_name:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {
                "success": False,
                "latency_ms": elapsed_ms,
                "model": "",
                "message": "No enabled model configured for this provider",
            }

        try:
            await asyncio.wait_for(
                litellm.acompletion(
                    model=model_name,
                    messages=[{"role": "user", "content": "hi"}],
                    api_key=api_key,
                    api_base=api_base_url,
                    max_tokens=1,
                ),
                timeout=10,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {
                "success": True,
                "latency_ms": elapsed_ms,
                "model": model_name,
                "message": "连接成功",
            }
        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {
                "success": False,
                "latency_ms": elapsed_ms,
                "model": model_name,
                "message": "Connection timed out (10s)",
            }
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                "Provider connectivity test failed",
                provider_id=str(provider_id),
                model=model_name,
                error=str(exc),
            )
            return {
                "success": False,
                "latency_ms": elapsed_ms,
                "model": model_name,
                "message": f"连接失败: {exc}",
            }

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
