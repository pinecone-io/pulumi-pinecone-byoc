import logging
import socket
import time

import requests

from .commands import pulumi_json


def data_plane_host(fqdn):
    return f"probe.svc.{fqdn}"


def private_data_plane_host(fqdn):
    return f"probe.svc.private.{fqdn}"


def cell_fqdn(project_dir):
    fqdn = pulumi_json("stack", "output", "--json", cwd=project_dir).get("cell_fqdn")
    assert fqdn, "the deploy exported no cell_fqdn"
    return fqdn


def assert_answers(host, timeout_seconds=900, poll_seconds=15, expect=200):
    deadline = time.time() + timeout_seconds
    last = ""
    while time.time() < deadline:
        try:
            address = socket.gethostbyname(host)
            status = requests.get(f"https://{host}/", timeout=15).status_code
            if status == expect:
                logging.info("[ingress] %s (%s) answered %s", host, address, status)
                return status
            last = f"answered {status} from {address}, wanted {expect}"
        except Exception as exc:  # noqa: BLE001 - every layer takes time to come up
            last = f"{type(exc).__name__}: {exc}"
        logging.info("[ingress] %s not answering yet: %s", host, last)
        time.sleep(poll_seconds)
    raise AssertionError(f"{host} never answered {expect} within {timeout_seconds}s: {last}")


def assert_data_plane_answers(project_dir):
    """The check a vanilla run makes, so every shape with public access makes it too."""
    return assert_answers(data_plane_host(cell_fqdn(project_dir)))


def assert_never_answers(host, settle_seconds=180, poll_seconds=15):
    """The other direction: a shape that asked for no public access must stay unreachable.

    Absence cannot be proven, so this watches for a bounded window after the deploy
    reports done - long enough for an internet-facing load balancer to finish coming
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
