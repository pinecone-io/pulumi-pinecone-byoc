import json
import logging
import os
import subprocess

from .redaction import redact


def run(*args, cwd, env=None):
    logging.info("$ %s  [%s]", " ".join(args), cwd)
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        env={**os.environ, **(env or {})},
    )
    lines = []
    for line in proc.stdout or []:
        lines.append(line)
        logging.info("%s", line.rstrip())
    proc.wait()
    output = "".join(lines)
    logging.info("-> exit %s", proc.returncode)
    if proc.returncode != 0:
        raise AssertionError(
            f"`{' '.join(args)}` failed with exit {proc.returncode} in {cwd}\n"
            f"{redact(output).strip() or '(no output)'}"
        )
    return output


def pulumi(*args, cwd, env=None):
    return run("pulumi", *args, cwd=cwd, env=env)


def pulumi_quiet(*args, cwd):
    """Run pulumi without logging its output, and report success rather than raise.

    A failure still logs what pulumi said - a swallowed exit code with no
    explanation is what made the old teardown impossible to diagnose.
    """
    result = subprocess.run(["pulumi", *args], cwd=cwd, text=True, capture_output=True, check=False)
    logging.info("$ pulumi %s -> exit %s", " ".join(args), result.returncode)
    if result.returncode != 0:
        logging.info("%s", redact(result.stderr or result.stdout or "(no output)").strip())
    return result.returncode == 0


def pulumi_json(*args, cwd):
    result = subprocess.run(["pulumi", *args], cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(
            f"`pulumi {' '.join(args)}` failed with exit {result.returncode}\n"
            f"{redact(result.stderr or result.stdout or '').strip()}"
        )
    return json.loads(result.stdout)
