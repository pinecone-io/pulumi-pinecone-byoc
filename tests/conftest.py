import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "setup"))

from e2e import log_config, settings  # noqa: E402


def pytest_addoption(parser):
    settings.add_options(parser)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    settings.remember_report(item, report)
    return report


def pytest_configure(config):
    log_config.start(config.option.keyword or config.option.markexpr or "all")
    region = settings.apply_to_environment(config)
    logging.info(
        f"=== session start: -m {config.option.markexpr!r} -k {config.option.keyword!r} "
        f"profile={os.environ.get('AWS_PROFILE', '<ambient credentials>')} region={region} "
        f"azs={settings.e2e_azs(config)} "
        f"control-plane={os.environ.get('PINECONE_GLOBAL_ENV', 'prod (module default)')}"
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    logging.info(f"selected {len(items)} test(s): {[i.name for i in items]}")
    needs_api_key = [
        i
        for i in items
        if i.get_closest_marker("e2e")
        or i.get_closest_marker("upgrade")
        or i.get_closest_marker("destroy")
    ]
    if needs_api_key and not os.environ.get("PINECONE_API_KEY"):
        message = (
            "PINECONE_API_KEY must be set to run e2e, upgrade and destroy tests; nothing "
            "was provisioned or destroyed. Export it and re-run."
        )
        logging.info(f"ABORT {message}")
        raise pytest.UsageError(message)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    stats = {key: len(value) for key, value in terminalreporter.stats.items() if key}
    logging.info(f"=== session end: exit={exitstatus} {stats}")
    for report in terminalreporter.stats.get("skipped", []):
        logging.info(f"SKIPPED {report.nodeid}: {report.longrepr}")
    for key in ("failed", "error"):
        for report in terminalreporter.stats.get(key, []):
            logging.info(f"{key.upper()} {report.nodeid}:\n{report.longreprtext}")
    print(f"\nrun log: {log_config.log_path()}")
