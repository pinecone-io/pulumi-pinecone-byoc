import json
import logging
import subprocess
import tempfile
from pathlib import Path

KINDS = ("validatingwebhookconfiguration", "mutatingwebhookconfiguration")


def _kubectl(kubeconfig, *args, timeout=60):
    return subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig, *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _kubeconfig_for(cluster, region):
    path = str(Path(tempfile.mkdtemp()) / "kubeconfig")
    written = subprocess.run(
        [
            "aws",
            "eks",
            "update-kubeconfig",
            "--name",
            cluster,
            "--region",
            region,
            "--kubeconfig",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if written.returncode != 0:
        logging.info("[webhooks] no kubeconfig for %s: %s", cluster, written.stderr.strip()[:200])
        return None
    return path


def _live_services(kubeconfig):
    listed = _kubectl(kubeconfig, "get", "svc", "-A", "-o", "json")
    if listed.returncode != 0:
        logging.info("[webhooks] cannot list services: %s", listed.stderr.strip()[:200])
        return None
    return {
        (svc["metadata"]["namespace"], svc["metadata"]["name"])
        for svc in json.loads(listed.stdout).get("items", [])
    }


def _backing_services(configuration):
    for hook in configuration.get("webhooks") or []:
        service = (hook.get("clientConfig") or {}).get("service")
        if service:
            yield service["namespace"], service["name"]


def drop_dangling_webhooks(cluster, region):
    """Delete admission webhooks whose backing service no longer exists.

    A webhook that fails closed rejects every write it admits, including the
    deletes a namespace performs while terminating. Tear down the release behind
    one - external-secrets, cert-manager - before the objects it guards, and the
    namespace hangs in Terminating with nothing left to remove, which Pulumi
    waits on until the job times out.
    """
    kubeconfig = _kubeconfig_for(cluster, region)
    if kubeconfig is None:
        return []

    live = _live_services(kubeconfig)
    if live is None:
        return []

    dropped = []
    for kind in KINDS:
        listed = _kubectl(kubeconfig, "get", kind, "-o", "json")
        if listed.returncode != 0:
            logging.info("[webhooks] cannot list %s: %s", kind, listed.stderr.strip()[:200])
            continue
        for configuration in json.loads(listed.stdout).get("items", []):
            name = configuration["metadata"]["name"]
            missing = sorted(
                f"{namespace}/{service}"
                for namespace, service in _backing_services(configuration)
                if (namespace, service) not in live
            )
            if not missing:
                continue
            deleted = _kubectl(kubeconfig, "delete", kind, name, "--ignore-not-found")
            logging.info(
                "[webhooks] %s/%s calls missing %s, deleted (exit %s)",
                kind,
                name,
                ", ".join(missing),
                deleted.returncode,
            )
            if deleted.returncode == 0:
                dropped.append(name)
    return dropped
