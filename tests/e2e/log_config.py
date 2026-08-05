import logging
import os
import re
from datetime import datetime
from pathlib import Path

from .paths import LOGS, REPO_ROOT
from .redaction import redact

_PATH = {"file": Path(os.environ.get("E2E_LOG") or REPO_ROOT / "e2e-test.log")}


class _Redact(logging.Filter):
    """Redact on the root logger, before any handler - including pytest's own."""

    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


class _OnlyOurs(logging.Filter):
    """Keep the run log to what this harness logged, not what a library did."""

    def filter(self, record):
        return record.name == "root"


def log_path():
    return _PATH["file"]


def start(selection):
    """One log file per session. The console copy is pytest's live log, see pytest.ini."""
    if not os.environ.get("E2E_LOG"):
        selection = re.sub(r"[^A-Za-z0-9_-]", "", selection.replace(" ", ""))[:40] or "all"
        started = datetime.now().strftime("%Y%m%d-%H%M%S")
        _PATH["file"] = LOGS / f"{started}-{selection}-{os.getpid()}.log"
    log_path().parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(log_path())
    handler.addFilter(_OnlyOurs())
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger().addFilter(_Redact())
    return log_path()
