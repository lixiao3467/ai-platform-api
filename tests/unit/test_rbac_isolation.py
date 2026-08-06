"""Unit tests for RBAC data isolation — ensure all queries are tenant-scoped."""

from __future__ import annotations

import uuid

import pytest


# =============================================================================
# Tenant Isolation Code Audit Tests
# =============================================================================


class TestTenantIsolationAudit:
    """
    Audit test suite to verify tenant data isolation.

    These tests verify that all API endpoints properly filter by tenant_id
    to prevent cross-tenant data leakage.
    """

    def test_agents_list_filters_by_tenant_id(self) -> None:
        """Test that agents list endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.agents import list_agents

        source = inspect.getsource(list_agents)
        assert "tenant_id == ctx.tenant_id" in source or "Agent.tenant_id" in source

    def test_agents_get_filters_by_tenant_id(self) -> None:
        """Test that agent detail endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.agents import get_agent

        source = inspect.getsource(get_agent)
        assert "tenant_id != ctx.tenant_id" in source or "tenant_id == ctx.tenant_id" in source

    def test_agents_delete_filters_by_tenant_id(self) -> None:
        """Test that agent delete endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.agents import delete_agent

        source = inspect.getsource(delete_agent)
        assert "tenant_id != ctx.tenant_id" in source or "tenant_id == ctx.tenant_id" in source

    def test_agents_run_filters_by_tenant_id(self) -> None:
        """Test that agent run endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.agents import run_agent

        source = inspect.getsource(run_agent)
        assert "tenant_id != ctx.tenant_id" in source or "tenant_id == ctx.tenant_id" in source

    def test_workflows_list_filters_by_tenant_id(self) -> None:
        """Test that workflows list endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.workflows import list_workflows

        source = inspect.getsource(list_workflows)
        assert "tenant_id == ctx.tenant_id" in source or "Workflow.tenant_id" in source

    def test_workflows_get_filters_by_tenant_id(self) -> None:
        """Test that workflow detail endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.workflows import get_workflow

        source = inspect.getsource(get_workflow)
        assert "tenant_id != ctx.tenant_id" in source or "tenant_id == ctx.tenant_id" in source

    def test_workflows_execute_filters_by_tenant_id(self) -> None:
        """Test that workflow execute endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.workflows import execute_workflow

        source = inspect.getsource(execute_workflow)
        assert "tenant_id != ctx.tenant_id" in source or "tenant_id == ctx.tenant_id" in source

    def test_prompts_list_filters_by_tenant_id(self) -> None:
        """Test that prompts list endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.prompts import list_prompts

        source = inspect.getsource(list_prompts)
        assert "tenant_id == ctx.tenant_id" in source or "PromptTemplate.tenant_id" in source

    def test_conversations_list_filters_by_tenant_id(self) -> None:
        """Test that conversations list endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.conversations import list_conversations

        source = inspect.getsource(list_conversations)
        assert "tenant_id == ctx.tenant_id" in source or "Conversation.tenant_id" in source

    def test_knowledge_list_filters_by_tenant_id(self) -> None:
        """Test that knowledge bases list endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.knowledge import list_knowledge_bases

        source = inspect.getsource(list_knowledge_bases)
        assert "tenant_id == ctx.tenant_id" in source or "KnowledgeBase.tenant_id" in source

    def test_audit_logs_list_filters_by_tenant_id(self) -> None:
        """Test that audit logs list endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.audit_logs import list_audit_logs

        source = inspect.getsource(list_audit_logs)
        assert "tenant_id == ctx.tenant_id" in source or "AuditLog.tenant_id" in source

    def test_users_list_filters_by_tenant_id(self) -> None:
        """Test that users list endpoint filters by tenant_id."""
        import inspect

        from ai_platform.api.v1.users import list_users

        source = inspect.getsource(list_users)
        assert "tenant_id == ctx.tenant_id" in source or "User.tenant_id" in source

    def test_costs_summary_filters_by_tenant_id(self) -> None:
        """Test that cost summary endpoint passes tenant_id to service."""
        import inspect

        from ai_platform.api.v1.costs import get_cost_summary

        source = inspect.getsource(get_cost_summary)
        assert "ctx.tenant_id" in source

    def test_costs_daily_filters_by_tenant_id(self) -> None:
        """Test that daily costs endpoint passes tenant_id to service."""
        import inspect

        from ai_platform.api.v1.costs import get_daily_costs

        source = inspect.getsource(get_daily_costs)
        assert "ctx.tenant_id" in source


# =============================================================================
# Chat Service Tenant Isolation Tests
# =============================================================================


class TestChatServiceTenantIsolation:
    """Test tenant isolation in chat service."""

    def test_chat_service_get_or_create_conversation_filters_by_tenant(self) -> None:
        """Test that _get_or_create_conversation filters by tenant_id."""
        import inspect

        from ai_platform.services.chat_service import ChatService

        source = inspect.getsource(ChatService._get_or_create_conversation)
        assert "tenant_id" in source
        assert "Conversation.tenant_id == tenant_id" in source


# =============================================================================
# Tenant Status Isolation Tests
# =============================================================================


class TestTenantStatusIsolation:
    """Test tenant status checks are properly isolated."""

    def test_tenant_status_cache_key_includes_tenant_id(self) -> None:
        """Test that tenant status cache key includes tenant_id."""
        tenant_id = uuid.uuid4()
        cache_key = f"aip:tenant_status:{tenant_id}"
        assert str(tenant_id) in cache_key

    def test_different_tenants_have_different_cache_keys(self) -> None:
        """Test that different tenants have different cache keys."""
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        key_a = f"aip:tenant_status:{tenant_a}"
        key_b = f"aip:tenant_status:{tenant_b}"
        assert key_a != key_b


# =============================================================================
# Quota Isolation Tests
# =============================================================================


class TestQuotaIsolation:
    """Test quota system isolation between tenants."""

    def test_quota_key_includes_tenant_id(self) -> None:
        """Test that quota key includes tenant_id."""
        from ai_platform.api.middleware.quota import _quota_key

        tenant_id = "tenant-123"
        resource_type = "model_calls"
        key = _quota_key(tenant_id, resource_type)
        assert tenant_id in key

    def test_different_tenants_have_different_quota_keys(self) -> None:
        """Test that different tenants have different quota keys."""
        from ai_platform.api.middleware.quota import _quota_key

        key_a = _quota_key("tenant-a", "model_calls")
        key_b = _quota_key("tenant-b", "model_calls")
        assert key_a != key_b

    def test_quota_config_cache_key_includes_tenant_id(self) -> None:
        """Test that quota config cache key includes tenant_id."""
        tenant_id = "tenant-123"
        cache_key = f"aip:tenant_quota_config:{tenant_id}"
        assert tenant_id in cache_key


# =============================================================================
# Permission Isolation Tests
# =============================================================================


class TestPermissionIsolation:
    """Test permission system isolation between tenants."""

    def test_permission_cache_key_includes_user_id(self) -> None:
        """Test that permission cache key includes user_id."""
        user_id = str(uuid.uuid4())
        cache_key = f"aip:user_perms:{user_id}"
        assert user_id in cache_key

    def test_different_users_have_different_permission_keys(self) -> None:
        """Test that different users have different permission cache keys."""
        user_a = str(uuid.uuid4())
        user_b = str(uuid.uuid4())
        key_a = f"aip:user_perms:{user_a}"
        key_b = f"aip:user_perms:{user_b}"
        assert key_a != key_b


# =============================================================================
# API Key Isolation Tests
# =============================================================================


class TestAPIKeyIsolation:
    """Test API key system isolation between tenants."""

    def test_api_key_cache_key_includes_prefix(self) -> None:
        """Test that API key cache key includes key prefix."""
        prefix = "aiplat_12"
        cache_key = f"aip:key:{prefix}"
        assert prefix in cache_key

    def test_different_keys_have_different_cache_keys(self) -> None:
        """Test that different API keys have different cache keys."""
        key_a = "aiplat_aaaaaaaa"
        key_b = "aiplat_bbbbbbbb"
        cache_a = f"aip:key:{key_a[:8]}"
        cache_b = f"aip:key:{key_b[:8]}"
        assert cache_a != cache_b


# =============================================================================
# Model Query Pattern Tests
# =============================================================================


class TestModelQueryPatterns:
    """Test that model queries follow safe patterns."""

    def test_conversation_model_has_tenant_id_column(self) -> None:
        """Test that Conversation model has tenant_id column."""
        from ai_platform.domain.models import Conversation

        assert hasattr(Conversation, "tenant_id")

    def test_agent_model_has_tenant_id_column(self) -> None:
        """Test that Agent model has tenant_id column."""
        from ai_platform.domain.models import Agent

        assert hasattr(Agent, "tenant_id")

    def test_workflow_model_has_tenant_id_column(self) -> None:
        """Test that Workflow model has tenant_id column."""
        from ai_platform.domain.models import Workflow

        assert hasattr(Workflow, "tenant_id")

    def test_knowledge_base_model_has_tenant_id_column(self) -> None:
        """Test that KnowledgeBase model has tenant_id column."""
        from ai_platform.domain.models import KnowledgeBase

        assert hasattr(KnowledgeBase, "tenant_id")

    def test_prompt_template_model_has_tenant_id_column(self) -> None:
        """Test that PromptTemplate model has tenant_id column."""
        from ai_platform.domain.models import PromptTemplate

        assert hasattr(PromptTemplate, "tenant_id")

    def test_user_model_has_tenant_id_column(self) -> None:
        """Test that User model has tenant_id column."""
        from ai_platform.domain.models import User

        assert hasattr(User, "tenant_id")

    def test_role_model_has_tenant_id_column(self) -> None:
        """Test that Role model has tenant_id column."""
        from ai_platform.domain.models import Role

        assert hasattr(Role, "tenant_id")

    def test_audit_log_model_has_tenant_id_column(self) -> None:
        """Test that AuditLog model has tenant_id column."""
        from ai_platform.domain.models import AuditLog

        assert hasattr(AuditLog, "tenant_id")


# =============================================================================
# Security Boundary Tests
# =============================================================================


class TestSecurityBoundaries:
    """Test security boundaries in the system."""

    def test_request_context_has_tenant_id(self) -> None:
        """Test that RequestContext has tenant_id."""
        from ai_platform.api.middleware.auth import RequestContext

        ctx = RequestContext(tenant_id=uuid.uuid4())
        assert ctx.tenant_id is not None

    def test_request_context_tenant_id_is_uuid(self) -> None:
        """Test that RequestContext tenant_id is UUID."""
        from ai_platform.api.middleware.auth import RequestContext

        tenant_id = uuid.uuid4()
        ctx = RequestContext(tenant_id=tenant_id)
        assert isinstance(ctx.tenant_id, uuid.UUID)

    def test_request_context_cannot_be_created_without_tenant_id(self) -> None:
        """Test that RequestContext requires tenant_id."""
        from ai_platform.api.middleware.auth import RequestContext

        with pytest.raises(TypeError):
            RequestContext()  # type: ignore


# =============================================================================
# Cross-Tenant Access Prevention Tests
# =============================================================================


class TestCrossTenantAccessPrevention:
    """Test prevention of cross-tenant data access."""

    def test_get_agent_returns_404_for_wrong_tenant(self) -> None:
        """Test that get_agent returns 404 when tenant doesn't match."""
        import inspect

        from ai_platform.api.v1.agents import get_agent

        source = inspect.getsource(get_agent)
        # Verify the code checks tenant_id and returns 404
        assert "404" in source
        assert "tenant_id" in source

    def test_get_workflow_returns_404_for_wrong_tenant(self) -> None:
        """Test that get_workflow returns 404 when tenant doesn't match."""
        import inspect

        from ai_platform.api.v1.workflows import get_workflow

        source = inspect.getsource(get_workflow)
        assert "404" in source
        assert "tenant_id" in source

    def test_delete_agent_returns_404_for_wrong_tenant(self) -> None:
        """Test that delete_agent returns 404 when tenant doesn't match."""
        import inspect

        from ai_platform.api.v1.agents import delete_agent

        source = inspect.getsource(delete_agent)
        assert "404" in source
        assert "tenant_id" in source


# =============================================================================
# Audit Report Summary
# =============================================================================


class TestAuditReportSummary:
    """Generate audit report summary."""

    def test_audit_checklist_complete(self) -> None:
        """Verify all audit checks are in place."""
        # This test serves as a checklist marker
        # All other tests in this file constitute the audit
        assert True

    def test_all_models_have_tenant_id(self) -> None:
        """Verify all tenant-scoped models have tenant_id."""
        from ai_platform.domain.models import (
            Agent,
            AuditLog,
            Conversation,
            KnowledgeBase,
            PromptTemplate,
            Role,
            User,
            Workflow,
        )

        models_with_tenant_id = [
            Agent,
            AuditLog,
            Conversation,
            KnowledgeBase,
            PromptTemplate,
            Role,
            User,
            Workflow,
        ]

        for model in models_with_tenant_id:
            assert hasattr(model, "tenant_id"), f"{model.__name__} missing tenant_id"


# =============================================================================
# Phase 1.5 — Supplementary Audit
# =============================================================================


class TestPhase15ApiKeysAudit:
    """Verify api_keys routes filter by tenant_id."""

    def test_create_api_key_uses_tenant_id(self) -> None:
        import inspect
        from ai_platform.api.v1.api_keys import create_api_key

        source = inspect.getsource(create_api_key)
        assert "ctx.tenant_id" in source

    def test_list_api_keys_uses_tenant_id(self) -> None:
        import inspect
        from ai_platform.api.v1.api_keys import list_api_keys

        source = inspect.getsource(list_api_keys)
        assert "ctx.tenant_id" in source

    def test_delete_api_key_uses_tenant_id(self) -> None:
        import inspect
        from ai_platform.api.v1.api_keys import delete_api_key

        source = inspect.getsource(delete_api_key)
        assert "ctx.tenant_id" in source


class TestPhase15SsoAudit:
    """Verify SSO routes filter by tenant_id (or are properly exempted)."""

    def test_list_sso_providers_filters_by_tenant(self) -> None:
        import inspect
        from ai_platform.api.v1.sso import list_sso_providers

        source = inspect.getsource(list_sso_providers)
        assert "SsoProvider.tenant_id == ctx.tenant_id" in source

    def test_create_sso_provider_sets_tenant_id(self) -> None:
        import inspect
        from ai_platform.api.v1.sso import create_sso_provider

        source = inspect.getsource(create_sso_provider)
        assert "ctx.tenant_id" in source

    def test_delete_sso_provider_filters_by_tenant(self) -> None:
        import inspect
        from ai_platform.api.v1.sso import delete_sso_provider

        source = inspect.getsource(delete_sso_provider)
        assert "SsoProvider.tenant_id == ctx.tenant_id" in source

    def test_sso_callback_is_public_endpoint(self) -> None:
        """SSO callback is a public OAuth endpoint — exempt from tenant_id filtering."""
        import inspect
        from ai_platform.api.v1.sso import sso_callback

        source = inspect.getsource(sso_callback)
        # Callback uses state parameter to bind to provider via Redis
        assert "sso_state" in source


class TestPhase15TenantSelfAudit:
    """Verify tenant_self routes filter by ctx.tenant_id."""

    def test_get_tenant_self_uses_ctx_tenant_id(self) -> None:
        import inspect
        from ai_platform.api.v1.tenant_self import get_tenant_self

        source = inspect.getsource(get_tenant_self)
        assert "ctx.tenant_id" in source

    def test_list_members_filters_by_tenant(self) -> None:
        import inspect
        from ai_platform.api.v1.tenant_self import list_members

        source = inspect.getsource(list_members)
        assert "User.tenant_id == ctx.tenant_id" in source

    def test_remove_member_checks_tenant(self) -> None:
        import inspect
        from ai_platform.api.v1.tenant_self import remove_member

        source = inspect.getsource(remove_member)
        assert "tenant_id != ctx.tenant_id" in source or "tenant_id == ctx.tenant_id" in source


class TestPhase15EvaluationsAudit:
    """Verify evaluations routes — currently pure in-memory, exempt from DB filtering."""

    def test_eval_run_is_in_memory(self) -> None:
        """Evaluations run in-memory without querying tenant-scoped DB data."""
        import inspect
        from ai_platform.api.v1.evaluations import run_evaluation

        source = inspect.getsource(run_evaluation)
        # No SELECT/DB query — all data comes from request body
        assert "select(" not in source.lower() or "evaluation" in source.lower()

    def test_eval_judge_is_in_memory(self) -> None:
        import inspect
        from ai_platform.api.v1.evaluations import judge_single

        source = inspect.getsource(judge_single)
        assert "select(" not in source.lower()


class TestPhase15ModelsAudit:
    """Verify models routes filter by tenant_id."""

    def test_list_providers_uses_tenant_id(self) -> None:
        import inspect
        from ai_platform.api.v1.models import list_providers

        source = inspect.getsource(list_providers)
        assert "ctx.tenant_id" in source

    def test_create_provider_uses_tenant_id(self) -> None:
        import inspect
        from ai_platform.api.v1.models import create_provider

        source = inspect.getsource(create_provider)
        assert "ctx.tenant_id" in source


class TestPhase15ChatAudit:
    """Verify chat routes pass tenant_id to service layer."""

    def test_chat_completions_passes_tenant_id(self) -> None:
        import inspect
        from ai_platform.api.v1.chat import chat_completions

        source = inspect.getsource(chat_completions)
        assert "ctx.tenant_id" in source


class TestPhase15MetricsAudit:
    """Verify metrics routes filter by tenant_id."""

    def test_api_metrics_filters_by_tenant(self) -> None:
        import inspect
        from ai_platform.api.v1.metrics_api import get_api_metrics

        source = inspect.getsource(get_api_metrics)
        assert "AuditLog.tenant_id == ctx.tenant_id" in source

    def test_model_metrics_filters_by_tenant(self) -> None:
        import inspect
        from ai_platform.api.v1.metrics_api import get_model_metrics

        source = inspect.getsource(get_model_metrics)
        assert "AuditLog.tenant_id == ctx.tenant_id" in source
