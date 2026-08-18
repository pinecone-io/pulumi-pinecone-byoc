"""What the snapshot taken at a failed deploy considers worth asking about.

The bug this covers cost a whole run: a pod was captured once, while it was still
Pending, and the crash it hit ten minutes later was never recorded.
"""

import json

from e2e import installer


def _pods(*items):
    return json.dumps({"items": list(items)})


def pod(namespace, name, phase, containers=None):
    return {
        "metadata": {"namespace": namespace, "name": name},
        "status": {"phase": phase, **({"containerStatuses": containers} if containers else {})},
    }


def _listing(monkeypatch, payload):
    class Result:
        returncode = 0
        stdout = payload
        stderr = ""

    monkeypatch.setattr(installer, "_kubectl", lambda *a, **k: Result())


def test_a_serving_pod_is_not_worth_a_postmortem(monkeypatch):
    _listing(monkeypatch, _pods(pod("pc-admin", "admin-1", "Running", [{"ready": True}])))

    assert installer._not_ready_pods("kubeconfig") == []


def test_a_crash_looping_pod_is_reported_with_the_reason(monkeypatch):
    _listing(
        monkeypatch,
        _pods(
            pod(
                "pc-index-builder-slab",
                "slab-1",
                "Running",
                [{"ready": False, "state": {"waiting": {"reason": "CrashLoopBackOff"}}}],
            )
        ),
    )

    assert installer._not_ready_pods("kubeconfig") == [
        ("pc-index-builder-slab", "slab-1", "CrashLoopBackOff")
    ]


def test_a_pod_with_no_node_reads_as_unschedulable(monkeypatch):
    _listing(monkeypatch, _pods(pod("pc-query-routers", "qr-1", "Pending")))

    assert installer._not_ready_pods("kubeconfig") == [
        ("pc-query-routers", "qr-1", installer.UNSCHEDULABLE)
    ]


def test_a_finished_job_pod_is_not_a_failure(monkeypatch):
    _listing(monkeypatch, _pods(pod("tooling", "pg-create-databases-1", "Succeeded")))

    assert installer._not_ready_pods("kubeconfig") == []


def test_a_running_pod_that_never_went_ready_is_reported(monkeypatch):
    """No waiting reason and no restarts: a failing readiness probe looks like this,
    and it is one of the shapes that leaves the install job waiting to its deadline."""
    _listing(monkeypatch, _pods(pod("pc-admin", "admin-2", "Running", [{"ready": False}])))

    assert installer._not_ready_pods("kubeconfig") == [("pc-admin", "admin-2", "NotReady")]


def test_a_reason_that_clears_inside_the_grace_is_never_captured():
    seen = {}
    pull = ("monitoring", "otel-collector-ingress-1", "ErrImagePull")

    assert installer._due_for_capture(seen, {pull}, now=0) == []
    assert installer._due_for_capture(seen, set(), now=20) == []
    assert seen == {}


def test_a_reason_that_outlives_the_grace_is_captured_once_it_has():
    seen = {}
    crash = ("pc-poolsnapper", "poolsnapper-1", "CrashLoopBackOff")

    assert installer._due_for_capture(seen, {crash}, now=0) == []
    assert installer._due_for_capture(seen, {crash}, now=installer.CAPTURE_GRACE_SECONDS) == [crash]


def test_a_pod_that_fails_again_is_timed_from_the_second_failure():
    seen = {}
    pull = ("pc-query-routers", "query-router-1", "ImagePullBackOff")

    installer._due_for_capture(seen, {pull}, now=0)
    installer._due_for_capture(seen, set(), now=30)

    assert installer._due_for_capture(seen, {pull}, now=60) == []
    assert installer._due_for_capture(seen, {pull}, now=60 + installer.CAPTURE_GRACE_SECONDS) == [
        pull
    ]
