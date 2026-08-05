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


def assert_never_answers(host, settle_seconds=180, poll_seconds=15):
    """The other direction: a shape that asked for no public access must stay unreachable.

    Absence cannot be proven, so this watches for a bounded window after the deploy
    reports done — long enough for an internet-facing load balancer to finish coming
    up, which is what a mistake here would look like.
    """
    deadline = time.time() + settle_seconds
    while time.time() < deadline:
        try:
            address = socket.gethostbyname(host)
            status = requests.get(f"https://{host}/", timeout=15).status_code
        except Exception as exc:  # noqa: BLE001 - not reachable is the outcome we want
            logging.info("[ingress] %s is not reachable from here: %s", host, type(exc).__name__)
            time.sleep(poll_seconds)
        else:
            raise AssertionError(
                f"{host} ({address}) answered {status} from outside the VPC, "
                "but this shape deployed with PINECONE_PUBLIC_ACCESS=false"
            )
    logging.info("[ingress] %s stayed unreachable for %ss", host, settle_seconds)
