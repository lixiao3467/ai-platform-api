"""Context engine — manage conversation context within model limits."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import structlog

from ai_platform.api.schemas.chat import ChatMessage

logger = structlog.get_logger()


# =============================================================================
# Token Counter
# =============================================================================


class TokenCounter:
    """
    Count tokens for messages using tiktoken.

    Supports multiple model encodings. Falls back to a rough
    character-based estimate if tiktoken is unavailable.
    """

    _encoders: dict[str, object] = {}

    @classmethod
    def count(cls, text: str, model: str = "gpt-4o") -> int:
        """Count tokens in a text string."""
        try:
            import tiktoken

            enc_name = cls._model_to_encoding(model)
            if enc_name not in cls._encoders:
                cls._encoders[enc_name] = tiktoken.get_encoding(enc_name)
            enc = cls._encoders[enc_name]
            return len(enc.encode(text))
        except ImportError:
            # Rough estimate: ~4 chars per token for English, ~2 for CJK
            cjk_count = sum(1 for c in text if ord(c) > 0x4E00)
            ascii_count = len(text) - cjk_count
            return (ascii_count // 4) + (cjk_count // 2)

    @classmethod
    def count_messages(cls, messages: list[ChatMessage], model: str = "gpt-4o") -> int:
        """Count tokens for a list of messages (includes per-message overhead)."""
        total = 0
        for msg in messages:
            total += 4  # message overhead: role + formatting
            if msg.content:
                total += cls.count(msg.content, model)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total += cls.count(tc.function.name, model)
                    total += cls.count(tc.function.arguments, model)
        total += 2  # assistant reply priming
        return total

    @staticmethod
    def _model_to_encoding(model: str) -> str:
        """Map model name to tiktoken encoding."""
        if model.startswith("gpt-4"):
            return "cl100k_base"
        if model.startswith("gpt-3.5"):
            return "cl100k_base"
        if "claude" in model:
            return "cl100k_base"  # Closest available
        if "qwen" in model:
            return "cl100k_base"
        return "cl100k_base"


# =============================================================================
# Context Strategies
# =============================================================================


@dataclass
class ContextConfig:
    """Context management configuration."""

    max_tokens: int = 4096           # Max tokens for context (excluding system prompt)
    strategy: str = "hybrid"         # sliding_window | token_truncate | summarize | hybrid
    max_messages: int = 20           # Max messages to include (sliding window)
    summary_model: str = "qwen-turbo"  # Model for summarization (cheap & fast)


class ContextStrategy(ABC):
    """Abstract base for context management strategies."""

    @abstractmethod
    async def build_context(
        self,
        messages: list[ChatMessage],
        config: ContextConfig,
        model: str,
    ) -> list[ChatMessage]:
        """Build a context window from message history."""
        ...


class SlidingWindowStrategy(ContextStrategy):
    """
    Keep the most recent N messages.

    Simple and effective for short conversations.
    Drops older messages entirely. System messages are always preserved.
    """

    async def build_context(
        self,
        messages: list[ChatMessage],
        config: ContextConfig,
        model: str,
    ) -> list[ChatMessage]:
        if len(messages) <= config.max_messages:
            return messages

        # Always preserve system messages
        system_msgs = [m for m in messages if m.role == "system"]
        non_system = [m for m in messages if m.role != "system"]

        # Keep the most recent non-system messages
        remaining_budget = max(0, config.max_messages - len(system_msgs))
        recent = non_system[-remaining_budget:] if remaining_budget else []

        return system_msgs + recent


class TokenTruncateStrategy(ContextStrategy):
    """
    Keep as many recent messages as fit within the token budget.

    More granular than sliding window — uses token count
    instead of message count as the limit. System messages are always preserved.
    """

    async def build_context(
        self,
        messages: list[ChatMessage],
        config: ContextConfig,
        model: str,
    ) -> list[ChatMessage]:
        if not messages:
            return []

        # Always preserve system messages (reserve their tokens first)
        system_msgs = [m for m in messages if m.role == "system"]
        non_system = [m for m in messages if m.role != "system"]

        system_tokens = sum(TokenCounter.count_messages([m], model) for m in system_msgs)
        remaining_budget = max(0, config.max_tokens - system_tokens)

        selected: list[ChatMessage] = []
        total_tokens = 0

        # Add messages from most recent to oldest
        for msg in reversed(non_system):
            msg_tokens = TokenCounter.count_messages([msg], model)
            if total_tokens + msg_tokens > remaining_budget:
                break
            selected.insert(0, msg)
            total_tokens += msg_tokens

        return system_msgs + selected


class SummarizeStrategy(ContextStrategy):
    """
    Summarize older messages, keep recent messages in full.

    Best for long conversations where early context still matters.
    Uses a cheap model to compress old messages into a summary.
    """

    async def build_context(
        self,
        messages: list[ChatMessage],
        config: ContextConfig,
        model: str,
    ) -> list[ChatMessage]:
        if len(messages) <= 4:
            return messages

        # Split: older messages → summarize, recent messages → keep
        split_point = max(2, len(messages) - config.max_messages)
        older = messages[:split_point]
        recent = messages[split_point:]

        # Check if recent messages already exceed budget
        recent_tokens = TokenCounter.count_messages(recent, model)
        if recent_tokens >= config.max_tokens:
            # Fall back to token truncation
            return await TokenTruncateStrategy().build_context(messages, config, model)

        # Summarize older messages
        summary = await self._summarize(older, config.summary_model)
        if not summary:
            return recent

        remaining_budget = config.max_tokens - recent_tokens
        summary_tokens = TokenCounter.count(summary, model)

        if summary_tokens > remaining_budget:
            # Truncate summary to fit
            words = summary.split()
            while words and TokenCounter.count(" ".join(words), model) > remaining_budget:
                words = words[:-5]
            summary = " ".join(words) + "..."

        summary_msg = ChatMessage(
            role="system",
            content=f"[Previous conversation summary]: {summary}",
        )

        return [summary_msg] + recent

    async def _summarize(self, messages: list[ChatMessage], model: str) -> str | None:
        """Summarize a list of messages using a cheap model."""
        try:
            from ai_platform.core.model_router.litellm_client import get_llm_client

            # Build conversation text
            conv_text = "\n".join(
                f"{m.role}: {m.content or ''}" for m in messages if m.content
            )

            llm = get_llm_client()
            from ai_platform.api.schemas.chat import ChatCompletionRequest

            response = await llm.chat(ChatCompletionRequest(
                model=model,
                messages=[
                    ChatMessage(
                        role="system",
                        content="Summarize the following conversation concisely. "
                                "Keep key facts, decisions, and context. Under 200 words.",
                    ),
                    ChatMessage(role="user", content=conv_text),
                ],
                temperature=0.3,
                max_tokens=300,
            ))

            if response.choices:
                return response.choices[0].message.content
            return None

        except Exception as e:
            logger.warning("Summarization failed, falling back", error=str(e))
            return None


class HybridStrategy(ContextStrategy):
    """
    Best of both worlds: summarize old + truncate recent.

    1. Summarize messages beyond the window
    2. Keep recent messages (within token budget)
    3. Combine: [summary] + [recent messages]
    """

    async def build_context(
        self,
        messages: list[ChatMessage],
        config: ContextConfig,
        model: str,
    ) -> list[ChatMessage]:
        if not messages:
            return []

        total_tokens = TokenCounter.count_messages(messages, model)

        # If everything fits, return as-is
        if total_tokens <= config.max_tokens and len(messages) <= config.max_messages:
            return messages

        # If too many messages but tokens fit, just slide window
        if total_tokens <= config.max_tokens:
            return messages[-config.max_messages:]

        # Need both summarization and truncation
        return await SummarizeStrategy().build_context(messages, config, model)


# =============================================================================
# Context Engine — Orchestrator
# =============================================================================

_STRATEGIES: dict[str, type[ContextStrategy]] = {
    "sliding_window": SlidingWindowStrategy,
    "token_truncate": TokenTruncateStrategy,
    "summarize": SummarizeStrategy,
    "hybrid": HybridStrategy,
}


class ContextEngine:
    """
    Manages conversation context to fit within model limits.

    Usage:
        engine = ContextEngine()
        context = await engine.build_context(
            system_prompt="You are a helpful assistant.",
            messages=history,
            model="qwen-max",
            config=ContextConfig(max_tokens=4096, strategy="hybrid"),
        )
    """

    async def build_context(
        self,
        system_prompt: str | None,
        messages: list[ChatMessage],
        model: str,
        config: ContextConfig | None = None,
    ) -> list[ChatMessage]:
        """
        Build the final context to send to the model.

        1. Reserve tokens for system prompt
        2. Apply selected strategy to message history
        3. Prepend system prompt
        4. Verify total fits within budget
        """
        config = config or ContextConfig()

        # Calculate system prompt tokens
        system_tokens = 0
        result_messages: list[ChatMessage] = []

        if system_prompt:
            system_tokens = TokenCounter.count(system_prompt, model) + 4
            result_messages.append(ChatMessage(role="system", content=system_prompt))

        # Adjust budget for system prompt
        adjusted_config = ContextConfig(
            max_tokens=max(256, config.max_tokens - system_tokens),
            strategy=config.strategy,
            max_messages=config.max_messages,
            summary_model=config.summary_model,
        )

        # Apply strategy
        strategy_cls = _STRATEGIES.get(config.strategy, HybridStrategy)
        strategy = strategy_cls()
        context_messages = await strategy.build_context(messages, adjusted_config, model)

        result_messages.extend(context_messages)

        # Final safety check
        final_tokens = TokenCounter.count_messages(result_messages, model)
        if final_tokens > config.max_tokens + 256:  # small tolerance
            logger.warning(
                "Context exceeds budget after strategy, force truncating",
                final_tokens=final_tokens,
                budget=config.max_tokens,
            )
            result_messages = await TokenTruncateStrategy().build_context(
                result_messages, config, model
            )

        logger.debug(
            "Context built",
            strategy=config.strategy,
            messages=len(result_messages),
            tokens=TokenCounter.count_messages(result_messages, model),
        )

        return result_messages
