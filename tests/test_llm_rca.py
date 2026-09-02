"""End-to-end test for LLM-powered RCA node (with rule-based fallback).

Tests all 5 mock diagnosis scenarios. When OPENAI_API_KEY is not set,
falls back to rule-based RCA (V1 logic). When set, uses LLM for RCA.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from infrastructure_agent.llm import get_llm_client
from infrastructure_agent.workflow.pod_crash_workflow import build_crashloop_graph
from infrastructure_agent.domain.models import AgentState, RequestContext

SCENARIOS = [
    {
        "name": "OOMKilled",
        "input": "nginx-oom 一直在重启，看起来像是内存问题",
        "expect_keywords": ["OOM", "内存", "limit", "memory"],
    },
    {
        "name": "ApplicationError",
        "input": "app-error 容器启动就退出",
        "expect_keywords": ["数据库", "连接", "启动", "connection"],
    },
    {
        "name": "ImagePullBackOff",
        "input": "image-pull 镜像一直拉不下来",
        "expect_keywords": ["镜像", "拉取", "registry", "image"],
    },
    {
        "name": "ConfigError",
        "input": "config-error Pod 挂载配置失败",
        "expect_keywords": ["Secret", "挂载", "配置", "卷"],
    },
    {
        "name": "FailedScheduling",
        "input": "sched-fail 一直 Pending",
        "expect_keywords": ["调度", "资源", "scheduling"],
    },
]


async def run_scenario(scenario: dict) -> dict:
    """Run one diagnosis scenario through the workflow graph."""
    name = scenario["name"]
    user_input = scenario["input"]
    keywords = scenario["expect_keywords"]

    graph = build_crashloop_graph()

    result = await graph.ainvoke(
        AgentState(
            request=RequestContext(user_input=user_input, user="test"),
        ).model_dump(),
        config={"recursion_limit": 50},
    )

    from infrastructure_agent.domain.models import AgentState as AS

    final_state = AS(**result)
    diagnosis = final_state.diagnosis

    rca_mode = result.get("_rca_mode", "unknown") 

    # Check keyword coverage
    root_cause_lower = diagnosis.root_cause.lower() if diagnosis else ""
    suggestion_lower = diagnosis.suggestion.lower() if diagnosis else ""
    combined = root_cause_lower + " " + suggestion_lower
    matched = [kw for kw in keywords if kw.lower() in combined]
    hit_rate = len(matched) / len(keywords) if keywords else 1.0

    return {
        "scenario": name,
        "rca_mode": rca_mode,
        "root_cause": diagnosis.root_cause if diagnosis else "N/A",
        "suggestion": diagnosis.suggestion if diagnosis else "N/A",
        "confidence": diagnosis.confidence if diagnosis else 0.0,
        "evidence_count": len(final_state.evidence),
        "keyword_match": f"{len(matched)}/{len(keywords)} ({hit_rate:.0%})",
        "passed": diagnosis is not None and hit_rate >= 0.5,
    }


async def main():
    llm = get_llm_client()
    print(f"LLM Available: {llm.available}")
    print(f"Model: {llm.model}")
    print(f"Base URL: {llm.base_url}")
    print("=" * 70)

    results = []
    for scenario in SCENARIOS:
        print(f"\nRunning: {scenario['name']}...")
        result = await run_scenario(scenario)
        results.append(result)
        status = "PASS" if result["passed"] else "FAIL"
        print(f"  [{status}] Mode: {result['rca_mode']} | "
              f"Confidence: {result['confidence']:.2f} | "
              f"Evidence: {result['evidence_count']} items")
        print(f"  Root Cause: {result['root_cause']}")
        print(f"  Keyword Match: {result['keyword_match']}")

    print("\n" + "=" * 70)
    passed = sum(1 for r in results if r["passed"])
    print(f"Summary: {passed}/{len(results)} scenarios passed")

    if passed < len(results):
        print("\nFAILED scenarios:")
        for r in results:
            if not r["passed"]:
                print(f"  - {r['scenario']}: {r['keyword_match']}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
