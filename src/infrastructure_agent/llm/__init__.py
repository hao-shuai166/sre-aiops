"""LLM Layer — Language model integration for Infrastructure Agent.

Provides:
- LLMClient: Async OpenAI-compatible client with graceful degradation
- Prompt templates: RCA analysis prompts for structured diagnosis output

Usage:
    from infrastructure_agent.llm import get_llm_client, build_rca_prompt, RCA_SYSTEM_PROMPT
"""

from infrastructure_agent.llm.client import LLMClient, get_llm_client
from infrastructure_agent.llm.prompts import RCA_SYSTEM_PROMPT, build_rca_prompt

__all__ = [
    "LLMClient",
    "get_llm_client",
    "RCA_SYSTEM_PROMPT",
    "build_rca_prompt",
]
