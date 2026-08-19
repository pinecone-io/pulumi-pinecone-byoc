import json
import logging
import re
import secrets
import subprocess
import tempfile
from pathlib import Path

PROBE_IMAGE = "curlimages/curl:8.11.1"
ATTEMPT = re.compile(r"attempt \d+: (\d{3})")

PROBE_SCRIPT = (
    "for i in $(seq 1 $ATTEMPTS); do "
    'code=$(curl -s -o /dev/null -m 15 -w "%{http_code}" "$URL" || true); '
    'echo "attempt $i: $code"; '
    'if [ "$code" = "$EXPECT" ]; then exit 0; fi; '
    "sleep $WAIT; done; exit 1"
)


def write_kubeconfig(cluster, region):
    path = str(Path(tempfile.mkdtemp()) / "kubeconfig")
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
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return path


def kubectl(kubeconfig, *args, timeout=180):
    """Run kubectl and log what it said.

    Output is logged, so this is for resources whose spec carries no credential -
    ingresses, services, pods. Never a secret.
    """
    result = subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig, *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    logging.info("$ kubectl %s -> exit %s", " ".join(args), result.returncode)
    return result


def ingresses_in(kubeconfig, namespace):
    listed = kubectl(kubeconfig, "-n", namespace, "get", "ingress", "-o", "json")
    if listed.returncode != 0:
        raise AssertionError(f"could not list ingresses in {namespace}: {listed.stderr.strip()}")
    items = json.loads(listed.stdout).get("items", [])
    return {i["metadata"]["name"]: i["metadata"].get("annotations", {}) for i in items}


def status_from_cluster(kubeconfig, url, image=PROBE_IMAGE, attempts=20, wait=30, expect=200):
    pod = f"pc-e2e-probe-{secrets.token_hex(3)}"
    result = kubectl(
        kubeconfig,
        "run",
        pod,
        "--rm",
        "--attach",
        "--restart=Never",
        "--quiet",
        f"--image={image}",
        f"--env=URL={url}",
        f"--env=ATTEMPTS={attempts}",
        f"--env=WAIT={wait}",
        f"--env=EXPECT={expect}",
        "--command",
        "--",
        "sh",
        "-c",
        PROBE_SCRIPT,
        timeout=attempts * (wait + 20) + 120,
    )
    logging.info("[probe] %s -> %s\n%s", url, result.returncode, result.stdout.strip())
    if result.returncode != 0 and not result.stdout:
        raise AssertionError(f"the probe pod never ran: {result.stderr.strip()}")

    codes = ATTEMPT.findall(result.stdout)
    answered = [code for code in codes if code != "000"]
    return int(answered[-1]) if answered else None
