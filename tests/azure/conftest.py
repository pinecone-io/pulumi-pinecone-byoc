"""These build Azure components, so they need the azure extra installed.

The aws job runs the whole of `tests` with `--extra aws` alone, and a module
that imports pulumi_azure_native at collection time fails that run before the
tests it selected get to start.
"""

import importlib.util

if importlib.util.find_spec("pulumi_azure_native") is None:
    collect_ignore_glob = ["*"]
