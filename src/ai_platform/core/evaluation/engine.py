"""Evaluation engine — RAG quality assessment + LLM-as-Judge."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from ai_platform.api.schemas.chat import ChatCompletionRequest, ChatMessage
from ai_platform.core.model_router.litellm_client import LiteLLMClient, get_llm_client

logger = structlog.get_logger()


# =============================================================================
# Evaluation Dataset
# =============================================================================


@dataclass
class EvalSample:
    """A single evaluation sample."""

    question: str
    expected_answer: str | None = None
    contexts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalDataset:
    """Collection of evaluation samples."""

    id: str
    name: str
    samples: list[EvalSample] = field(default_factory=list)


# =============================================================================
# Evaluation Result
# =============================================================================


@dataclass
class EvalMetricResult:
    """Result of a single metric evaluation."""

    metric: str
    score: float  # 0.0 - 1.0
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SampleEvalResult:
    """Evaluation result for a single sample."""

    sample_index: int
    question: str
    generated_answer: str
    retrieved_contexts: list[str]
    metrics: list[EvalMetricResult]
    overall_score: float = 0.0


@dataclass
class EvalRunResult:
    """Complete evaluation run result."""

    run_id: str
    dataset_name: str
    total_samples: int
    completed_samples: int
    failed_samples: int
    sample_results: list[SampleEvalResult]
    aggregate_scores: dict[str, float]  # metric_name → average score
    duration_seconds: float
    model: str


# =============================================================================
# RAG Metrics
# =============================================================================


class RAGEvaluator:
    """
    Evaluates RAG pipeline quality using LLM-as-Judge.

    Metrics:
    - Faithfulness: Is the answer faithful to the retrieved context?
    - Answer Relevancy: Is the answer relevant to the question?
    - Context Precision: Are the retrieved contexts precise (no noise)?
    - Context Recall: Did we retrieve all necessary contexts?
    - Answer Correctness: Is the answer factually correct?
    """

    def __init__(self, llm_client: LiteLLMClient | None = None, judge_model: str = "gpt-4o") -> None:
        self._llm = llm_client or get_llm_client()
        self._judge_model = judge_model

    async def evaluate_sample(
        self,
        question: str,
        generated_answer: str,
        contexts: list[str],
        expected_answer: str | None = None,
    ) -> list[EvalMetricResult]:
        """Evaluate a single RAG sample across all metrics."""
        # Run all metrics concurrently
        results = await asyncio.gather(
            self._evaluate_faithfulness(generated_answer, contexts),
            self._evaluate_answer_relevancy(question, generated_answer),
            self._evaluate_context_precision(question, contexts),
            self._evaluate_context_recall(question, contexts, expected_answer),
            self._evaluate_answer_correctness(question, generated_answer, expected_answer),
            return_exceptions=True,
        )

        metrics = []
        for r in results:
            if isinstance(r, EvalMetricResult):
                metrics.append(r)
            elif isinstance(r, Exception):
                logger.warning("Metric evaluation failed", error=str(r))

        return metrics

    async def _evaluate_faithfulness(
        self, answer: str, contexts: list[str]
    ) -> EvalMetricResult:
        """Is the answer faithful to the provided contexts?"""
        context_text = "\n---\n".join(contexts)
        prompt = f"""You are an AI evaluator. Rate how FAITHFUL the answer is to the given contexts.

Contexts:
{context_text}

Answer:
{answer}

Rate from 0 to 1:
- 1.0: Answer is fully supported by the contexts
- 0.5: Answer is partially supported
- 0.0: Answer contradicts or hallucinates beyond contexts

Respond with JSON only: {{"score": 0.X, "reason": "brief explanation"}}"""

        result = await self._judge(prompt)
        return EvalMetricResult(
            metric="faithfulness",
            score=result.get("score", 0),
            reason=result.get("reason"),
        )

    async def _evaluate_answer_relevancy(
        self, question: str, answer: str
    ) -> EvalMetricResult:
        """Is the answer relevant to the question?"""
        prompt = f"""Rate how RELEVANT the answer is to the question.

Question: {question}
Answer: {answer}

Rate from 0 to 1:
- 1.0: Answer directly addresses the question
- 0.5: Answer is somewhat related
- 0.0: Answer is completely off-topic

Respond with JSON only: {{"score": 0.X, "reason": "brief explanation"}}"""

        result = await self._judge(prompt)
        return EvalMetricResult(
            metric="answer_relevancy",
            score=result.get("score", 0),
            reason=result.get("reason"),
        )

    async def _evaluate_context_precision(
        self, question: str, contexts: list[str]
    ) -> EvalMetricResult:
        """Are the retrieved contexts precise and relevant?"""
        context_text = "\n---\n".join(
            f"[{i+1}]: {c[:500]}" for i, c in enumerate(contexts)
        )
        prompt = f"""Rate the PRECISION of the retrieved contexts for answering the question.

Question: {question}
Contexts:
{context_text}

Rate from 0 to 1:
- 1.0: All contexts are directly relevant to the question
- 0.5: Some contexts are relevant, some are noise
- 0.0: Contexts are irrelevant to the question

Respond with JSON only: {{"score": 0.X, "reason": "brief explanation"}}"""

        result = await self._judge(prompt)
        return EvalMetricResult(
            metric="context_precision",
            score=result.get("score", 0),
            reason=result.get("reason"),
        )

    async def _evaluate_context_recall(
        self, question: str, contexts: list[str], expected_answer: str | None
    ) -> EvalMetricResult:
        """Did we retrieve all necessary information?"""
        if not expected_answer:
            return EvalMetricResult(
                metric="context_recall", score=-1.0,
                reason="Skipped: no expected answer provided",
            )

        context_text = "\n---\n".join(contexts)
        prompt = f"""Rate whether the retrieved contexts contain enough information to produce the expected answer.

Question: {question}
Expected Answer: {expected_answer}
Retrieved Contexts:
{context_text}

Rate from 0 to 1:
- 1.0: Contexts contain all information needed for the expected answer
- 0.5: Contexts contain partial information
- 0.0: Contexts are missing critical information

Respond with JSON only: {{"score": 0.X, "reason": "brief explanation"}}"""

        result = await self._judge(prompt)
        return EvalMetricResult(
            metric="context_recall",
            score=result.get("score", 0),
            reason=result.get("reason"),
        )

    async def _evaluate_answer_correctness(
        self, question: str, answer: str, expected_answer: str | None
    ) -> EvalMetricResult:
        """Is the answer factually correct compared to expected?"""
        if not expected_answer:
            return EvalMetricResult(
                metric="answer_correctness", score=-1.0,
                reason="Skipped: no expected answer provided",
            )

        prompt = f"""Rate the CORRECTNESS of the answer compared to the expected answer.

Question: {question}
Expected Answer: {expected_answer}
Actual Answer: {answer}

Rate from 0 to 1:
- 1.0: Answer matches expected answer in key facts
- 0.5: Answer is partially correct
- 0.0: Answer is factually wrong

Respond with JSON only: {{"score": 0.X, "reason": "brief explanation"}}"""

        result = await self._judge(prompt)
        return EvalMetricResult(
            metric="answer_correctness",
            score=result.get("score", 0),
            reason=result.get("reason"),
        )

    async def _judge(self, prompt: str) -> dict[str, Any]:
        """Call the judge LLM and parse JSON response."""
        import json

        messages = [
            ChatMessage(role="system", content="You are a precise AI evaluation judge. Respond with valid JSON only."),
            ChatMessage(role="user", content=prompt),
        ]
        request = ChatCompletionRequest(
            model=self._judge_model,
            messages=messages,
            temperature=0.1,
            max_tokens=200,
        )

        response = await self._llm.chat(request)
        content = response.choices[0].message.content or ""

        try:
            # Try to extract JSON from the response
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            return json.loads(content)
        except (json.JSONDecodeError, IndexError):
            logger.warning("Judge response parse failed", content=content[:200])
            return {"score": 0.0, "reason": "Failed to parse judge response"}


# =============================================================================
# Evaluation Runner
# =============================================================================


class EvaluationRunner:
    """
    Runs a complete evaluation against a dataset.

    For each sample:
    1. Run RAG pipeline (retrieve + generate)
    2. Evaluate with LLM-as-Judge
    3. Aggregate scores
    """

    def __init__(
        self,
        evaluator: RAGEvaluator | None = None,
        judge_model: str = "gpt-4o",
        generate_model: str = "qwen-max",
    ) -> None:
        self._evaluator = evaluator or RAGEvaluator(judge_model=judge_model)
        self._generate_model = generate_model

    async def run(
        self,
        dataset: EvalDataset,
        *,
        retrieve_fn=None,
    ) -> EvalRunResult:
        """
        Run evaluation on the entire dataset.

        Args:
            dataset: The evaluation dataset
            retrieve_fn: Optional async function(question) -> list[str] for retrieval.
                        If None, uses contexts from the sample directly.
        """
        run_id = str(uuid.uuid4())
        start_time = time.time()
        sample_results: list[SampleEvalResult] = []
        failed = 0

        for i, sample in enumerate(dataset.samples):
            try:
                # Step 1: Retrieve contexts (or use provided ones)
                if retrieve_fn:
                    contexts = await retrieve_fn(sample.question)
                else:
                    contexts = sample.contexts

                # Step 2: Generate answer
                answer = await self._generate_answer(sample.question, contexts)

                # Step 3: Evaluate
                metrics = await self._evaluator.evaluate_sample(
                    sample.question, answer, contexts, sample.expected_answer,
                )

                # Calculate overall score
                valid_scores = [m.score for m in metrics if m.score >= 0]
                overall = sum(valid_scores) / len(valid_scores) if valid_scores else 0

                sample_results.append(SampleEvalResult(
                    sample_index=i,
                    question=sample.question,
                    generated_answer=answer,
                    retrieved_contexts=contexts,
                    metrics=metrics,
                    overall_score=round(overall, 3),
                ))

                logger.info(
                    "Sample evaluated",
                    index=i,
                    overall=round(overall, 3),
                    metrics=len(metrics),
                )

            except Exception as e:
                logger.error("Sample evaluation failed", index=i, error=str(e))
                failed += 1

        # Aggregate scores
        aggregate = self._aggregate_scores(sample_results)

        return EvalRunResult(
            run_id=run_id,
            dataset_name=dataset.name,
            total_samples=len(dataset.samples),
            completed_samples=len(sample_results),
            failed_samples=failed,
            sample_results=sample_results,
            aggregate_scores=aggregate,
            duration_seconds=round(time.time() - start_time, 1),
            model=self._generate_model,
        )

    async def _generate_answer(self, question: str, contexts: list[str]) -> str:
        """Generate an answer using contexts."""
        from ai_platform.core.model_router.litellm_client import get_llm_client

        context_text = "\n---\n".join(contexts) if contexts else "No context available."

        messages = [
            ChatMessage(
                role="system",
                content="Answer the question based ONLY on the provided context. "
                        "If the context doesn't contain relevant information, say so.",
            ),
            ChatMessage(
                role="user",
                content=f"Context:\n{context_text}\n\nQuestion: {question}",
            ),
        ]

        llm = get_llm_client()
        request = ChatCompletionRequest(
            model=self._generate_model,
            messages=messages,
            temperature=0.3,
        )
        response = await llm.chat(request)
        return response.choices[0].message.content or "" if response.choices else ""

    @staticmethod
    def _aggregate_scores(results: list[SampleEvalResult]) -> dict[str, float]:
        """Calculate average score per metric across all samples."""
        metric_totals: dict[str, list[float]] = {}

        for result in results:
            for m in result.metrics:
                if m.score >= 0:  # Skip skipped metrics (-1)
                    metric_totals.setdefault(m.metric, []).append(m.score)

        return {
            metric: round(sum(scores) / len(scores), 3)
            for metric, scores in metric_totals.items()
        }
