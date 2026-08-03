"""Test script to verify all modules can be imported."""
import sys
import traceback

# Add src to path
sys.path.insert(0, "src")

modules_to_test = [
    "ai_platform.config",
    "ai_platform.domain.models",
    "ai_platform.infra.database.connection",
    "ai_platform.infra.cache.redis_client",
    "ai_platform.infra.storage.minio_client",
    "ai_platform.infra.secrets.crypto",
    "ai_platform.core.model_router.router",
    "ai_platform.core.model_router.providers.base",
    "ai_platform.core.model_router.providers.openai_provider",
    "ai_platform.core.model_router.providers.anthropic_provider",
    "ai_platform.core.knowledge.engine",
    "ai_platform.core.knowledge.parsers.base",
    "ai_platform.core.knowledge.store.milvus_store",
    "ai_platform.core.knowledge.retrieval.hybrid_retriever",
    "ai_platform.core.agent.runtime",
    "ai_platform.core.agent.tools.registry",
    "ai_platform.core.workflow.engine",
    "ai_platform.core.prompt.manager",
    "ai_platform.services.chat_service",
    "ai_platform.services.knowledge_service",
    "ai_platform.services.agent_service",
    "ai_platform.services.workflow_service",
    "ai_platform.services.prompt_service",
    "ai_platform.services.provider_service",
    "ai_platform.services.cost_service",
    "ai_platform.api.middleware.auth",
    "ai_platform.api.middleware.rate_limit",
    "ai_platform.api.v1.chat",
    "ai_platform.api.v1.knowledge",
    "ai_platform.api.v1.agents",
    "ai_platform.api.v1.workflows",
    "ai_platform.api.v1.prompts",
    "ai_platform.api.v1.models",
    "ai_platform.observability.metrics",
    "ai_platform.observability.metrics_middleware",
    "ai_platform.observability.tracing",
    "ai_platform.observability.logging",
]

errors = []
successes = []

print("=" * 60)
print("Testing module imports...")
print("=" * 60)

for module_name in modules_to_test:
    try:
        __import__(module_name)
        successes.append(module_name)
        print(f"[OK] {module_name}")
    except Exception as e:
        errors.append((module_name, str(e), traceback.format_exc()))
        print(f"[FAIL] {module_name}: {e}")

print("\n" + "=" * 60)
print(f"Results: {len(successes)} passed, {len(errors)} failed")
print("=" * 60)

if errors:
    print("\nFailed modules:")
    for module_name, error, tb in errors:
        print(f"\n--- {module_name} ---")
        print(error)
        print(tb)

sys.exit(0 if not errors else 1)
