import contextlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .aws import find_cluster_for_vpc, list_clusters

NAMESPACE = "pc-control-plane"
UNSCHEDULABLE = "Unschedulable"

UNHEALTHY_WAITING = {
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerConfigError",
    "CreateContainerError",
}

_STATUS: dict[str, Any] = {"cluster": None, "pods": 0, "restarts": 0, "nodes_logged": 0.0}


def _heartbeat(stop_event, seconds):
    while not stop_event.wait(seconds):
        logging.info(
            f"[pinetools] watchdog alive - cluster={_STATUS['cluster']} "
            f"pods_followed={_STATUS['pods']} restarts={_STATUS['restarts']}"
        )


def supervise_pinetools_logs(vpc_id, stop_event, poll_seconds=15, heartbeat_seconds=300):
    """Watchdog around the log streamer.

    The streamer must survive the whole deploy; if it ever returns or raises it
    is restarted, so a single transient failure cannot leave the run unobserved.
    """
    if not shutil.which("kubectl"):
        logging.info("[pinetools] kubectl not on PATH - installer logs will NOT be captured")
        return

    threading.Thread(target=_heartbeat, args=(stop_event, heartbeat_seconds), daemon=True).start()

    while not stop_event.is_set():
        try:
            stream_pinetools_logs(vpc_id, stop_event, poll_seconds)
        except BaseException as exc:  # noqa: BLE001 - the watchdog outlives any failure
            logging.info(f"[pinetools] streamer crashed: {type(exc).__name__}: {exc}")
        if stop_event.is_set():
            break
        _STATUS["restarts"] += 1
        logging.info(f"[pinetools] restarting streamer (restart #{_STATUS['restarts']})")
        stop_event.wait(5)
    logging.info("[pinetools] watchdog stopped")


def _kubectl(kubeconfig, *args, namespace=NAMESPACE, timeout=60):
    scope = ["-n", namespace] if namespace else []
    return subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig, *scope, *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _unhealthy_pods(kubeconfig):
    """Pods stuck in a crash/pull loop anywhere in the cluster."""
    listed = _kubectl(kubeconfig, "get", "pods", "-A", "-o", "json", namespace=None)
    if listed.returncode != 0:
        return []
    try:
        items = json.loads(listed.stdout).get("items", [])
    except ValueError:
        return []
    found = []
    for pod in items:
        meta = pod.get("metadata", {})
        status = pod.get("status", {})
        statuses = status.get("containerStatuses") or []

        if status.get("phase") == "Pending" and not statuses:
            unschedulable = any(
                c.get("type") == "PodScheduled" and c.get("status") == "False"
                for c in status.get("conditions") or []
            )
            if unschedulable:
                found.append((meta.get("namespace"), meta.get("name"), UNSCHEDULABLE))
            continue

        for container in statuses:
            reason = (container.get("state", {}).get("waiting") or {}).get("reason")
            if reason in UNHEALTHY_WAITING or container.get("restartCount", 0) >= 3:
                found.append((meta.get("namespace"), meta.get("name"), reason or "restarting"))
                break
    return found


def capture_nodes(kubeconfig, every_seconds=300):
    now = time.monotonic()
    if now - _STATUS["nodes_logged"] < every_seconds:
        return
    _STATUS["nodes_logged"] = now
    listed = _kubectl(
        kubeconfig,
        "get",
        "nodes",
        "-L",
        "pinecone.io/index-builder,pinecone.io/query-router,node.kubernetes.io/instance-type",
        namespace=None,
    )
    logging.info(
        "[crashloop] nodes (exit %s)\n%s",
        listed.returncode,
        listed.stdout or listed.stderr or "(nothing)",
    )


def capture_unhealthy_pod(kubeconfig, namespace, pod, reason):
    """Save why a pod is failing, while the cluster still exists.

    The previous container's logs are the useful part: a crash-looping pod's
    current instance is usually too young to have logged the failure, and all of
    it disappears when the cluster is torn down.

    A pod that has not been scheduled has no container to ask, so it gets the
    describe and nothing else - the events there are the whole answer.
    """
    logging.info("[crashloop] %s/%s %s", namespace, pod, reason)
    if reason == UNSCHEDULABLE:
        described = _kubectl(kubeconfig, "describe", "pod", pod, "-n", namespace, namespace=None)
        logging.info(
            "[crashloop] %s/%s describe (exit %s)\n%s",
            namespace,
            pod,
            described.returncode,
            described.stdout or described.stderr or "(nothing)",
        )
        return

    for label, args in (
        (
            "previous logs",
            [
                "logs",
                pod,
                "-n",
                namespace,
                "--all-containers=true",
                "--prefix",
                "--previous",
                "--tail",
                "400",
            ],
        ),
        (
            "current logs",
            ["logs", pod, "-n", namespace, "--all-containers=true", "--prefix", "--tail", "400"],
        ),
        ("describe", ["describe", "pod", pod, "-n", namespace]),
    ):
        result = _kubectl(kubeconfig, *args, namespace=None, timeout=90)
        logging.info(
            "[crashloop] %s/%s %s (exit %s)\n%s",
            namespace,
            pod,
            label,
            result.returncode,
            result.stdout or result.stderr or "(nothing)",
        )


def capture_failed_deploy(region, limit=20):
    cluster = _STATUS["cluster"]
    if cluster is None:
        logging.info("[postmortem] no cluster was ever found - nothing to snapshot")
        return
    try:
        kubeconfig = str(Path(tempfile.mkdtemp()) / "kubeconfig")
        subprocess.run(
            [
                "aws",
                "eks",
                "update-kubeconfig",
                "--name",
                cluster,
                "--region",
                region,
                "--kubeconfig",
                kubeconfig,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception as exc:  # noqa: BLE001
        logging.info("[postmortem] could not reach %s: %s: %s", cluster, type(exc).__name__, exc)
        return

    logging.info("[postmortem] %s: what was not ready when the deploy failed", cluster)
    try:
        capture_nodes(kubeconfig, every_seconds=0)
        for namespace, pod, reason in _not_ready_pods(kubeconfig)[:limit]:
            capture_unhealthy_pod(kubeconfig, namespace, pod, reason)
    except Exception as exc:  # noqa: BLE001
        logging.info("[postmortem] snapshot incomplete: %s: %s", type(exc).__name__, exc)


def _not_ready_pods(kubeconfig):
    listed = _kubectl(kubeconfig, "get", "pods", "-A", "-o", "json", namespace=None, timeout=120)
    if listed.returncode != 0:
        logging.info("[postmortem] could not list pods: %s", listed.stderr.strip())
        return []
    found = []
    for pod in json.loads(listed.stdout).get("items", []):
        meta, status = pod.get("metadata", {}), pod.get("status", {})
        phase = status.get("phase")
        if phase == "Succeeded":
            continue
        statuses = status.get("containerStatuses") or []
        if phase == "Running" and statuses and all(c.get("ready") for c in statuses):
            continue
        waiting = [(c.get("state", {}).get("waiting") or {}).get("reason") for c in statuses]
        reason = next((r for r in waiting if r), None)
        if reason is None and not statuses:
            reason = UNSCHEDULABLE if phase == "Pending" else phase
        found.append((meta.get("namespace"), meta.get("name"), reason or "NotReady"))
    return found


def _ensure_kubeconfig(vpc_id, region, stop_event, poll_seconds, baseline=None):
    """Keep trying until the cluster exists and a kubeconfig is written."""
    while not stop_event.is_set():
        try:
            name, status = find_cluster_for_vpc(vpc_id, region, baseline)
            if name and status == "ACTIVE":
                path = str(Path(tempfile.mkdtemp()) / "kubeconfig")
                written = subprocess.run(
                    [
                        "aws",
                        "eks",
                        "update-kubeconfig",
                        "--name",
                        name,
                        "--region",
                        region,
                        "--kubeconfig",
                        path,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if written.returncode == 0:
                    logging.info(f"[pinetools] streaming logs from cluster {name}")
                    _STATUS["cluster"] = name
                    return path
                logging.info(
                    f"[pinetools] update-kubeconfig failed, retrying: {written.stderr.strip()[:200]}"
                )
        except Exception as exc:  # noqa: BLE001 - keep polling through transient AWS errors
            logging.info(f"[pinetools] cluster lookup retrying: {type(exc).__name__}: {exc}")
        stop_event.wait(poll_seconds)
    return None


def _follow_pod(kubeconfig, pod, stop_event):
    """Attach to one pod, reattaching until it reaches a terminal phase.

    A pod spends minutes in PodInitializing (wait-for-regcred), where `kubectl
    logs -f` exits immediately, so a single attempt captures nothing.
    """
    logging.info("[pinetools] following %s", pod)

    captured_any = False
    while not stop_event.is_set():
        phase = ""
        got_output = False
        try:
            probe = _kubectl(kubeconfig, "get", "pod", pod, "-o", "jsonpath={.status.phase}")
            if probe.returncode != 0:
                logging.info("[pinetools] %s no longer exists, stopping follow", pod)
                break
            phase = probe.stdout.strip()
            history = ["--tail", "0"] if captured_any else ["--since", "3h"]
            proc = subprocess.Popen(
                [
                    "kubectl",
                    "--kubeconfig",
                    kubeconfig,
                    "-n",
                    NAMESPACE,
                    "logs",
                    "-f",
                    pod,
                    "--all-containers=true",
                    "--prefix",
                    *history,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in proc.stdout or []:
                got_output = True
                logging.info("[%s] %s", pod, line.rstrip())
            proc.wait()
        except Exception as exc:  # noqa: BLE001 - reattach on any failure
            logging.info("[pinetools] %s attach retrying: %s: %s", pod, type(exc).__name__, exc)
        if got_output and proc.returncode == 0:
            captured_any = True
        if phase in ("Succeeded", "Failed"):
            break
        if stop_event.wait(5):
            break

    with contextlib.suppress(Exception):
        logging.info(
            "[%s] describe pod\n%s", pod, _kubectl(kubeconfig, "describe", "pod", pod).stdout
        )
    logging.info("[pinetools] %s finished", pod)


def stream_pinetools_logs(vpc_id, stop_event, poll_seconds=15):
    """Follow pinetools install/uninstall pod logs for the whole deploy.

    Every layer retries: a transient AWS error, a missing cluster or a pod that
    is not ready yet must never end the stream.
    """
    if not shutil.which("kubectl"):
        logging.info("[pinetools] kubectl not on PATH - installer logs will NOT be captured")
        return

    region = os.environ["AWS_REGION"]
    baseline = None
    if vpc_id is None:
        try:
            baseline = list_clusters(region)
            logging.info(
                f"[pinetools] vanilla run; ignoring pre-existing clusters: {sorted(baseline)}"
            )
        except Exception as exc:  # noqa: BLE001
            logging.info(f"[pinetools] could not snapshot clusters: {type(exc).__name__}: {exc}")
    kubeconfig = _ensure_kubeconfig(vpc_id, region, stop_event, poll_seconds, baseline)
    if kubeconfig is None:
        return

    followed = {}
    captured_unhealthy = set()
    while not stop_event.is_set():
        try:
            listed = _kubectl(kubeconfig, "get", "pods", "-o", "name")
            for line in listed.stdout.splitlines():
                pod = line.removeprefix("pod/").strip()
                if not pod.startswith("pinetools-") or pod in followed:
                    continue
                worker = threading.Thread(
                    target=_follow_pod, args=(kubeconfig, pod, stop_event), daemon=True
                )
                worker.start()
                followed[pod] = worker
                _STATUS["pods"] = len(followed)
        except Exception as exc:  # noqa: BLE001 - keep polling for new pods
            logging.info(f"[pinetools] pod poll retrying: {type(exc).__name__}: {exc}")

        try:
            for namespace, pod, reason in _unhealthy_pods(kubeconfig):
                if (namespace, pod, reason) in captured_unhealthy:
                    continue
                captured_unhealthy.add((namespace, pod, reason))
                if reason == UNSCHEDULABLE:
                    capture_nodes(kubeconfig)
                capture_unhealthy_pod(kubeconfig, namespace, pod, reason)
        except Exception as exc:  # noqa: BLE001 - diagnostics must not stop the stream
            logging.info(f"[crashloop] scan retrying: {type(exc).__name__}: {exc}")

        stop_event.wait(poll_seconds)
