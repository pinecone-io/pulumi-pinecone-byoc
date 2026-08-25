import ast
import importlib.util
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "setup"))

from e2e import log_config, settings  # noqa: E402

CLOUD_SDKS = {"aws": "pulumi_aws", "gcp": "pulumi_gcp", "azure": "pulumi_azure_native"}
UNINSTALLED = {
    name
    for cloud, sdk in CLOUD_SDKS.items()
    for name in (sdk, f"pulumi_pinecone_byoc.{cloud}")
    if importlib.util.find_spec(sdk) is None
}


def _imported_modules(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module


def _needs_uninstalled_sdk(path):
    return any(
        name in UNINSTALLED or name.startswith(tuple(f"{n}." for n in UNINSTALLED))
        for name in _imported_modules(path)
    )


def pytest_ignore_collect(collection_path, config):
    if not UNINSTALLED:
        return None
    if collection_path.is_dir():
        conftest = collection_path / "conftest.py"
        if conftest.is_file() and _needs_uninstalled_sdk(conftest):
            return True
    elif collection_path.suffix == ".py" and _needs_uninstalled_sdk(collection_path):
        return True
    return None


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
        i for i in items if i.get_closest_marker("e2e") or i.get_closest_marker("destroy")
    ]
    if needs_api_key and not os.environ.get("PINECONE_API_KEY"):
        message = (
            "PINECONE_API_KEY must be set to run e2e tests; nothing was provisioned or "
            "destroyed. Export it and re-run."
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
