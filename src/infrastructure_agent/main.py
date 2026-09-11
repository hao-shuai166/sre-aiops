"""Infrastructure Agent API — FastAPI application.

Provides a POST /diagnose endpoint that accepts natural language questions
about infrastructure issues and returns structured diagnosis results
with full evidence chain.

Also serves the built frontend (frontend/dist) when present — the Vue
console is then available at the root path of the same origin, no
separate web server needed.

Start: uvicorn infrastructure_agent.main:app --reload
"""

import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

from infrastructure_agent.agent import diagnose

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Infrastructure Agent",
    description="AI-Native Infrastructure Operations Platform — Kubernetes 智能故障诊断",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DiagnoseRequest(BaseModel):
    question: str = Field(
        description="Natural language question about an infrastructure issue",
        examples=["为什么 nginx-oom Pod 一直重启？"],
    )
    user: str = Field(default="anonymous", description="User identifier")


class DiagnoseResponse(BaseModel):
    problem: str = Field(description="Problem summary")
    root_cause: str = Field(description="Root cause analysis result")
    evidence: list[dict] = Field(description="Evidence chain supporting the diagnosis")
    suggestion: str = Field(description="Actionable suggestion to fix the problem")
    confidence: float = Field(description="Overall diagnosis confidence (0.0-1.0)")
    rca_mode: str = Field(
        default="unknown",
        description="How the conclusion was produced: llm / error / unknown",
    )
    reasoning_trace: list[dict] = Field(description="Step-by-step reasoning trace")


@app.post("/diagnose", response_model=DiagnoseResponse)
async def diagnose_endpoint(req: DiagnoseRequest) -> DiagnoseResponse:
    """Diagnose an infrastructure issue from a natural language question.

    Example request:
        POST /diagnose
        {"question": "为什么 nginx-oom Pod 一直重启？"}

    Example response:
        {
            "problem": "Pod CrashLoopBackOff",
            "root_cause": "Memory Limit 不足导致 OOMKilled",
            "evidence": [
                {"id": "ev001", "type": "PodStatus", ...},
                {"id": "ev002", "type": "KubernetesEvent", ...}
            ],
            "suggestion": "增加 Pod memory limit 或优化应用内存使用",
            "confidence": 0.90,
            "reasoning_trace": [...]
        }
    """
    try:
        result = await diagnose(req.question, user=req.user)
    except Exception as exc:
        logger.exception("Unhandled error in diagnose_endpoint: %s", exc)
        return DiagnoseResponse(
            problem="internal_error",
            root_cause=f"服务内部异常: {exc}",
            evidence=[],
            suggestion="请查看服务端日志获取详细错误信息",
            confidence=0.0,
            reasoning_trace=[],
        )
    return DiagnoseResponse(**result)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "infrastructure-agent", "version": "0.2.0"}


# ---- Frontend hosting (built Vue console) ----
# Mounted only when frontend/dist exists, so backend-only environments
# (e.g. CI running tests without a frontend build) start cleanly.
# FRONTEND_DIST env var overrides the path (used in Docker, where the package
# lives in site-packages and the console is copied to /app/frontend/dist).
_DIST = Path(
    os.environ.get("FRONTEND_DIST")
    or (Path(__file__).resolve().parent.parent.parent / "frontend" / "dist")
)
if _DIST.is_dir():
    # html=True enables SPA history fallback (serve index.html for unknown paths)
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
    logger.info("Serving frontend console from %s", _DIST)
else:
    logger.info("frontend/dist not found — API-only mode")


def main():
    """Entry point for running the API server."""
    uvicorn.run(
        "infrastructure_agent.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
