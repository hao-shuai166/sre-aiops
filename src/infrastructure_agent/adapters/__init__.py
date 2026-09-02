"""Adapters — Infrastructure client adapters with mock fallback for development."""

from infrastructure_agent.adapters.k8s_client import KubernetesClient

__all__ = ["KubernetesClient"]
