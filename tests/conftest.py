"""Shared pytest setup: make the setup/ wizard importable as `wizard`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "setup"))
