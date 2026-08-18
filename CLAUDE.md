# Working in this repository

## Never echo a secret

This module handles live credentials: the customer's Pinecone API key, a cpgw API
key, a Datadog API key, and cloud credentials. The e2e harness streams command
output to a log file, prints it to stdout, and uploads it
as a CI artifact — and installer pod output is echoed into the GitHub job log,
which anyone with read access to the repository can download for 90 days.

So the rule is not "be careful with logs". It is: **a secret must never reach
stdout, a log file, or a command line.**

### What that means in practice

**Read secrets with `subprocess.run`, never through `run()`.** `tests/e2e/commands.py`
has both: `run()` logs the command line and every line of output, while
`subprocess.run(..., capture_output=True)` keeps output in the process - that is
what `pulumi_quiet()` and `pulumi_json()` are built on. Anything that can print a
credential uses the second one, and passes only what it needs to a caller.

**Redaction is a logging filter, not a call you remember to make.** `tests/e2e/
log_config.py` attaches it to the root logger, so it runs before any handler,
including pytest's own capture. Write to a file by hand and you are outside it.

**Pass secrets in the environment, not in arguments.** `run()` logs argv, and argv
is visible to every process on the machine. The harness gives the wizard
`PINECONE_API_KEY` via `env=`, and CI gives it through `env:` on the step.

**Do not add these to a script, a test, or a workflow step:**

- `pulumi stack export --show-secrets` piped anywhere that is logged, printed, or
  uploaded — including `jq` one-liners in a workflow step
- `pulumi config get <key> --show-secrets`, or `cat` of a generated
  `Pulumi.<stack>.yaml`
- `kubectl get secret ... -o yaml`, or `kubectl describe` on a resource whose spec
  carries literal credentials
- `set -x` / `bash -x` in any step that has secrets in its environment
- `echo "$PINECONE_API_KEY"`, and the same for cpgw, Datadog and Pulumi tokens

**Use secret-typed config.** Stack config for credentials is written with
`pulumi config set --secret`, so it is encrypted at rest and Pulumi masks it as
`[secret]` in `up` and `destroy` output. A value passed as a plain config key is
not masked anywhere.

**Keep fabricated credentials in tests below full length.** A realistic-length
token in a test fixture is not distinguishable from a live one by GitHub's push
protection, and it blocks the push. `tests/test_redaction.py` documents the
lengths it uses and why.

**GitHub masks only registered secrets.** A token *derived* from a secret — a
registry token fetched with a cpgw key, a kubeconfig, a presigned URL — is not
masked, and neither is anything a pod prints.

### Before adding anything that logs

Ask what the command can print on its unhappy path, not just its happy one. An
error body is the usual leak: a failing HTTP call that echoes the request, a
provider that dumps inputs on conflict, a pod that logs the credential it could
not use.

### Checking a log or artifact

Scan for the shapes rather than reading it all:

```bash
grep -aEo 'pcsk_[A-Za-z0-9_]{8,}|pul-[0-9a-f]{20,}|(AKIA|ASIA)[0-9A-Z]{16}|\b[0-9a-f]{32}\b|ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}|postgres(ql)?://[^[:space:]]+:[^[:space:]]+@' <file> | sort -u
```

A 32-hex hit is usually a Datadog API key; a 40-hex hit is usually a git SHA.
`***` means GitHub masked a registered secret, which is the expected outcome.

If a secret did reach a log: rotate it first, then delete the artifact and the run.
Deleting the log without rotating leaves the secret valid in anyone's local copy.

## Other conventions

- Comments and docstrings only where they explain something the code cannot;
  prefer a commit message for rationale.
- Imports at module level. `boto3` in `setup/wizard.py` is the exception: the
  wizard runs before the cloud extra is installed.
- `ruff check`, `ruff format --check` and `ty check` all run in CI. Run all three.
