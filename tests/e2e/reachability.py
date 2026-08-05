import logging
import socket
import time

import requests


def data_plane_host(environment):
    return f"probe.svc.{environment}.pinecone.io"


def assert_answers(host, timeout_seconds=900, poll_seconds=15):
    deadline = time.time() + timeout_seconds
    last = ""
    while time.time() < deadline:
        try:
            address = socket.gethostbyname(host)
            status = requests.get(f"https://{host}/", timeout=15).status_code
            logging.info("[ingress] %s (%s) answered %s", host, address, status)
            return status
        except Exception as exc:  # noqa: BLE001 - every layer takes time to come up
            last = f"{type(exc).__name__}: {exc}"
            logging.info("[ingress] %s not answering yet: %s", host, last)
            time.sleep(poll_seconds)
    raise AssertionError(f"{host} never answered within {timeout_seconds}s: {last}")
