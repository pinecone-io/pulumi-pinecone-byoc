import json
import logging
import os
import subprocess
import time

import requests

INDEX_API_VERSION = os.environ.get("PINECONE_API_VERSION", "2025-04")


def project_credentials(project_dir):
    # --show-secrets prints every secret in the stack: subprocess.run, never run()
    exported = subprocess.run(
        ["pulumi", "stack", "export", "--show-secrets"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if exported.returncode != 0:
        logging.info(f"[cleanup] stack export failed: {(exported.stderr or '').strip()[:200]}")
        return None, None
    try:
        deployment = json.loads(exported.stdout).get("deployment", {})
    except ValueError as exc:
        logging.info(f"[cleanup] could not parse stack export: {exc}")
        return None, None
    for resource in deployment.get("resources", []):
        if "pc-api-key" not in resource.get("urn", ""):
            continue
        outputs = resource.get("outputs", {})
        return outputs.get("value"), outputs.get("api_url")
    return None, None


def _list_indexes(api_url, headers):
    listed = requests.get(f"{api_url}/indexes", headers=headers, timeout=30)
    if listed.status_code != 200:
        logging.info(
            f"[cleanup] listing indexes returned {listed.status_code}: {listed.text[:200]}"
        )
        return None
    return [index["name"] for index in listed.json().get("indexes", [])]


def delete_project_indexes(project_dir, timeout_seconds=600, poll_seconds=15):
    try:
        api_key, api_url = project_credentials(project_dir)
        if not api_key or not api_url:
            logging.info("[cleanup] no project api key in state, skipping index cleanup")
            return
        headers = {"Api-Key": api_key, "X-Pinecone-Api-Version": INDEX_API_VERSION}

        names = _list_indexes(api_url, headers)
        if names is None:
            return
        if not names:
            logging.info("[cleanup] project has no leftover indexes")
            return

        logging.info(f"[cleanup] deleting leftover indexes: {', '.join(names)}")
        for name in names:
            deleted = requests.delete(f"{api_url}/indexes/{name}", headers=headers, timeout=120)
            logging.info(f"[cleanup] delete index {name} -> {deleted.status_code}")

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            remaining = _list_indexes(api_url, headers)
            if remaining is None:
                return
            if not remaining:
                logging.info("[cleanup] all indexes deleted")
                return
            logging.info(f"[cleanup] waiting for index deletion: {', '.join(remaining)}")
            time.sleep(poll_seconds)
        logging.info(
            f"[cleanup] indexes still present after {timeout_seconds}s - "
            "project deletion will fail with 412"
        )
    except Exception as exc:  # noqa: BLE001 - cleanup must never fail the teardown
        logging.info(f"[cleanup] index cleanup failed: {type(exc).__name__}: {exc}")
