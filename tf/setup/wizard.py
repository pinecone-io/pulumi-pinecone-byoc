#!/usr/bin/env python3
"""Generate terraform.tfvars.json for a BYOC Terraform example."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

# Mirrors the hard-coded default in the Pulumi-side wizard
# (../../setup/wizard.py: PINECONE_VERSION). Bump both together.
DEFAULT_PINECONE_VERSION = "main-1b955e2"


DEFAULTS = {
    "aws": {
        "region": "us-east-1",
        "availability_zones": ["us-east-1a", "us-east-1b"],
        "vpc_cidr": "10.0.0.0/16",
    },
    "gcp": {
        "region": "us-central1",
        "availability_zones": ["us-central1-a", "us-central1-b"],
        "vpc_cidr": "10.112.0.0/12",
    },
    "azure": {
        "region": "eastus",
        "availability_zones": ["1", "2"],
        "vpc_cidr": "10.0.0.0/16",
    },
}


def ask(prompt: str, default: str | None = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    if not value and default is not None:
        return default
    while not value:
        value = input(f"{prompt}: ").strip()
    return value


def ask_bool(prompt: str, default: bool) -> bool:
    default_label = "y" if default else "n"
    value = input(f"{prompt} [{default_label}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "true", "1"}


def ask_list(prompt: str, default: list[str]) -> list[str]:
    raw = ask(prompt, ",".join(default))
    return [item.strip() for item in raw.split(",") if item.strip()]


def preflight(cloud: str) -> list[str]:
    tools = ["terraform", "kubectl"]
    if cloud == "aws":
        tools.append("aws")
    elif cloud == "gcp":
        tools.append("gcloud")
    else:
        tools.append("az")

    missing = [tool for tool in tools if shutil.which(tool) is None]
    if shutil.which("go") is None:
        missing.append("go")
    return missing


def build_values(cloud: str) -> dict:
    defaults = DEFAULTS[cloud]
    values = {
        "pinecone_api_key": ask("Pinecone API key"),
        "pinecone_version": ask("Pinecone version", DEFAULT_PINECONE_VERSION),
        "region": ask("Region", defaults["region"]),
        "availability_zones": ask_list("Availability zones, comma-separated", defaults["availability_zones"]),
        "vpc_cidr": ask("VPC/VNet CIDR", defaults["vpc_cidr"]),
        "kubernetes_version": ask("Kubernetes version", "1.33"),
        "parent_dns_zone_name": ask("Parent DNS zone", "byoc.pinecone.io"),
        "public_access_enabled": ask_bool("Enable public access", True),
        "deletion_protection": ask_bool("Enable deletion protection", True),
        "api_url": ask("Pinecone API URL", "https://api.pinecone.io"),
        "global_env": ask("Global environment", "prod"),
        "auth0_domain": ask("Auth0 domain", "https://login.pinecone.io"),
    }

    if cloud == "gcp":
        values["project"] = ask("GCP project")
        values["amp_aws_account_id"] = ask("AMP AWS account ID", "713131977538")
        values["labels"] = {}
    elif cloud == "azure":
        values["subscription_id"] = ask("Azure subscription ID")
        values["amp_aws_account_id"] = ask("AMP AWS account ID", "713131977538")
        values["gcp_project"] = ask("GCP project for helmfile templates", "production-pinecone")
        values["tags"] = {}
    else:
        values["gcp_project"] = ask("GCP project for helmfile templates", "production-pinecone")
        values["tags"] = {}

    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cloud", nargs="?", choices=sorted(DEFAULTS))
    parser.add_argument("--force", action="store_true", help="overwrite existing terraform.tfvars.json")
    args = parser.parse_args()

    cloud = args.cloud
    if cloud is None:
        cloud = ask("Cloud (aws, gcp, azure)", "aws")
        if cloud not in DEFAULTS:
            print(f"unsupported cloud: {cloud}", file=sys.stderr)
            return 2

    missing = preflight(cloud)
    if missing:
        print("missing local tools: " + ", ".join(missing), file=sys.stderr)
        print("continuing; install them before running terraform apply", file=sys.stderr)

    values = build_values(cloud)
    output_path = EXAMPLES / cloud / "terraform.tfvars.json"
    if output_path.exists() and not args.force:
        print(f"{output_path} already exists; rerun with --force to overwrite", file=sys.stderr)
        return 1

    output_path.write_text(json.dumps(values, indent=2) + "\n")
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
