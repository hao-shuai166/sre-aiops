"""Unit tests for target_resolver — user-input → TargetContext parsing.

Covers the extraction from pod_crash_workflow (item ③):
- pod name parsing (hyphen priority / prefix fallback / status-word exclusion);
- namespace parsing (explicit context / bare alias / default fallback);
- cluster label from K8S_CLUSTER env with "prod" default.
"""

import pytest

from infrastructure_agent.agent.target_resolver import (
    TargetContext,
    default_cluster,
    parse_namespace,
    parse_pod_name,
    resolve_target,
)


# ---- parse_pod_name ----

def test_pod_name_hyphen_token_wins():
    assert parse_pod_name("sched-failpod 一直 Pending") == "sched-failpod"


def test_pod_name_prefix_fallback():
    assert parse_pod_name("nginx 好像挂了") == "nginx"


def test_pod_name_status_words_excluded():
    # 全是状态词时回退到默认 nginx，不会把 crashloopbackoff 当 pod 名
    assert parse_pod_name("pod crashloopbackoff error") == "nginx"


def test_pod_name_fallback_when_nothing_matches():
    assert parse_pod_name("为什么服务不可用") == "nginx"


# ---- parse_namespace ----

def test_namespace_explicit_chinese_context():
    assert parse_namespace("看一下 kube-system 命名空间里的 pod") == "kube-system"


def test_namespace_bare_alias():
    assert parse_namespace("monitoring 里的 pod 一直重启") == "monitoring"


def test_namespace_default_fallback():
    assert parse_namespace("nginx-oom 挂了") == "default"


def test_namespace_alias_stage():
    assert parse_namespace("stage 命名空间") == "staging"


# ---- cluster / resolve_target ----

def test_default_cluster_without_env(monkeypatch):
    monkeypatch.delenv("K8S_CLUSTER", raising=False)
    assert default_cluster() == "prod"


def test_default_cluster_from_env(monkeypatch):
    monkeypatch.setenv("K8S_CLUSTER", "kind-vm")
    assert default_cluster() == "kind-vm"


def test_resolve_target_combines_all(monkeypatch):
    monkeypatch.setenv("K8S_CLUSTER", "kind-vm")
    t = resolve_target("kube-system 命名空间里 nginx-oom 一直 CrashLoopBackOff")
    assert isinstance(t, TargetContext)
    assert t.cluster == "kind-vm"
    assert t.namespace == "kube-system"
    assert t.pod == "nginx-oom"


def test_resolve_target_defaults(monkeypatch):
    monkeypatch.delenv("K8S_CLUSTER", raising=False)
    t = resolve_target("帮我看下这个 pod 怎么了")
    assert (t.cluster, t.namespace, t.pod) == ("prod", "default", "nginx")
