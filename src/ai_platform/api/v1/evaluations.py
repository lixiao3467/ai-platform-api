"""Evaluations API — /api/v1/evaluations/*."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse
from ai_platform.core.evaluation.engine import (
    EvalDataset,
    EvalSample,
    EvaluationRunner,
    RAGEvaluator,
)

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class EvalSampleSchema(BaseModel):
    question: str
    expected_answer: str | None = None
    contexts: list[str] = Field(default_factory=list)


class EvalDatasetRequest(BaseModel):
    name: str
    samples: list[EvalSampleSchema]


class EvalRunRequest(BaseModel):
    dataset: EvalDatasetRequest
    judge_model: str = Field(default="gpt-4o", description="Model used as evaluation judge")
    generate_model: str = Field(default="qwen-max", description="Model being evaluated")


class JudgeSingleRequest(BaseModel):
    question: str
    answer: str
    contexts: list[str] = Field(default_factory=list)
    expected_answer: str | None = None
    judge_model: str = Field(default="gpt-4o")


class EvalMetricOut(BaseModel):
    metric: str
    score: float
    reason: str | None = None


class SampleResultOut(BaseModel):
    sample_index: int
    question: str
    generated_answer: str
    overall_score: float
    metrics: list[EvalMetricOut]


class EvalRunOut(BaseModel):
    run_id: str
    dataset_name: str
    total_samples: int
    completed_samples: int
    failed_samples: int
    aggregate_scores: dict[str, float]
    duration_seconds: float
    model: str
    sample_results: list[SampleResultOut]


class EvalExportRequest(BaseModel):
    run_id: str
    format: str = Field(default="csv", pattern="^(csv|json)$", description="导出格式")


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/run", response_model=ApiResponse[EvalRunOut], dependencies=[Depends(require_permission("evaluation.manage"))])
async def run_evaluation(
    req: EvalRunRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """
    Run a full evaluation against a dataset.

    For each sample: retrieve → generate → evaluate with LLM-as-Judge.
    Returns per-sample scores and aggregate metrics.
    """
    dataset = EvalDataset(
        id=str(uuid.uuid4()),
        name=req.dataset.name,
        samples=[
            EvalSample(
                question=s.question,
                expected_answer=s.expected_answer,
                contexts=s.contexts,
            )
            for s in req.dataset.samples
        ],
    )

    if not dataset.samples:
        raise HTTPException(status_code=422, detail="Dataset must have at least one sample")

    evaluator = RAGEvaluator(judge_model=req.judge_model)
    runner = EvaluationRunner(
        evaluator=evaluator,
        judge_model=req.judge_model,
        generate_model=req.generate_model,
    )

    result = await runner.run(dataset)

    return ApiResponse(data=EvalRunOut(
        run_id=result.run_id,
        dataset_name=result.dataset_name,
        total_samples=result.total_samples,
        completed_samples=result.completed_samples,
        failed_samples=result.failed_samples,
        aggregate_scores=result.aggregate_scores,
        duration_seconds=result.duration_seconds,
        model=result.model,
        sample_results=[
            SampleResultOut(
                sample_index=sr.sample_index,
                question=sr.question,
                generated_answer=sr.generated_answer,
                overall_score=sr.overall_score,
                metrics=[
                    EvalMetricOut(metric=m.metric, score=m.score, reason=m.reason)
                    for m in sr.metrics
                ],
            )
            for sr in result.sample_results
        ],
    ))


@router.post("/judge", response_model=ApiResponse, dependencies=[Depends(require_permission("evaluation.manage"))])
async def judge_single(
    req: JudgeSingleRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Run LLM-as-Judge evaluation on a single Q&A pair."""
    evaluator = RAGEvaluator(judge_model=req.judge_model)
    metrics = await evaluator.evaluate_sample(
        req.question, req.answer, req.contexts, req.expected_answer
    )

    return ApiResponse(data={
        "metrics": [
            {"metric": m.metric, "score": m.score, "reason": m.reason}
            for m in metrics
        ],
    })


@router.post(
    "/export",
    summary="导出评估结果",
    description="将评估运行结果导出为 CSV 或 JSON。包含每个样本的评分和汇总指标。",
    dependencies=[Depends(require_permission("evaluation.manage"))],
    responses={
        200: {"description": "文件下载"},
        404: {"description": "评估运行不存在（注意：评估结果当前为运行时内存数据）"},
    },
)
async def export_evaluation(
    req: EvalExportRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """
    Export evaluation results.

    NOTE: Current evaluation runner does not persist results to DB.
    This endpoint returns a 501 placeholder until result persistence is implemented.
    Use the /run response directly for data export until then.
    """
    from fastapi import HTTPException
    raise HTTPException(
        status_code=501,
        detail="评估结果持久化尚未实现。请在调用 /run 时直接保存响应数据。",
    )
