"""Mask credential shapes before anything is written down.

This repository is public, so its job logs and artifacts are too, and GitHub masks
only the secrets it was given. A key one of our own providers minted, a registry
token, a kubeconfig, a connection string - none of those are masked, and installer
pods print what they were handed, including on the paths where they failed to use
it. Every line the harness writes goes through redact() first.
"""

import re

SECRET_SHAPES = re.compile(
    r"pcsk_[A-Za-z0-9_]{8,}"
    r"|pul-[0-9a-f]{20,}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}"
    r"|ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"
    r"|postgres(?:ql)?://[^\s:/]+:[^\s@]+@"
    r"|\b[0-9a-f]{32}\b"
)


def redact(text):
    """Mask anything shaped like a credential. Over-masking a log beats leaking one."""
    return SECRET_SHAPES.sub("[redacted]", text)
