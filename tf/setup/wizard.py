#!/usr/bin/env python3
"""Generate terraform.tfvars.json for a BYOC Terraform example."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


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


REQUIRED_GCP_SERVICES = [
    "alloydb.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "dns.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sts.googleapis.com",
    "storage.googleapis.com",
]

REQUIRED_AZURE_RESOURCE_PROVIDERS = [
    "Microsoft.Authorization",
    "Microsoft.Compute",
    "Microsoft.ContainerService",
    "Microsoft.DBforPostgreSQL",
    "Microsoft.KeyVault",
    "Microsoft.ManagedIdentity",
    "Microsoft.Network",
    "Microsoft.Storage",
]


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
        "pinecone_version": ask("Pinecone version"),
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
        values["project"] = ask("GCP project ID (not display name)")
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


def gcloud(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gcloud", *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def aws(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["aws", *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def az(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["az", *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def validate_aws(values: dict) -> int:
    identity = aws(["sts", "get-caller-identity", "--output", "json"])
    if identity.returncode != 0:
        print("cannot access AWS with the current credentials", file=sys.stderr)
        print(identity.stderr.strip(), file=sys.stderr)
        print("run: aws sts get-caller-identity", file=sys.stderr)
        return 1

    region = values["region"]
    zones = values["availability_zones"]
    azs = aws(["ec2", "describe-availability-zones", "--region", region, "--zone-names", *zones, "--output", "json"])
    if azs.returncode != 0:
        print(f"cannot validate AWS region/AZs for region {region!r}", file=sys.stderr)
        print(azs.stderr.strip(), file=sys.stderr)
        print(f"check region and zones: {', '.join(zones)}", file=sys.stderr)
        return 1

    return 0


def validate_gcp(values: dict) -> int:
    project = values["project"]
    result = gcloud(["projects", "describe", project, "--format=value(projectId)"])
    if result.returncode != 0:
        print(f"cannot access GCP project ID {project!r}", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        print("Use the project ID, not the display name, and make sure your gcloud account has access.", file=sys.stderr)
        return 1

    active = gcloud(["config", "get-value", "project"])
    if active.returncode == 0 and active.stdout.strip() != project:
        print(f"warning: active gcloud project is {active.stdout.strip()!r}, but tfvars uses {project!r}", file=sys.stderr)
        print(f"run: gcloud config set project {project}", file=sys.stderr)

    enabled = gcloud(["services", "list", "--enabled", "--project", project, "--format=value(config.name)"])
    if enabled.returncode != 0:
        print("warning: could not list enabled GCP services", file=sys.stderr)
        print(enabled.stderr.strip(), file=sys.stderr)
        print(f"run: gcloud services enable serviceusage.googleapis.com --project {project}", file=sys.stderr)
        return 0

    enabled_services = set(enabled.stdout.split())
    missing = [service for service in REQUIRED_GCP_SERVICES if service not in enabled_services]
    if missing:
        print("warning: required GCP services are not enabled yet; Terraform will try to enable them", file=sys.stderr)
        print("manual command:", file=sys.stderr)
        print(f"gcloud services enable {' '.join(missing)} --project {project}", file=sys.stderr)

    print(f"run: gcloud auth application-default set-quota-project {project}", file=sys.stderr)
    return 0


def validate_azure(values: dict) -> int:
    subscription_id = values["subscription_id"]
    account = az(["account", "show", "--subscription", subscription_id, "--output", "json"])
    if account.returncode != 0:
        print(f"cannot access Azure subscription {subscription_id!r}", file=sys.stderr)
        print(account.stderr.strip(), file=sys.stderr)
        print(f"run: az account show --subscription {subscription_id}", file=sys.stderr)
        return 1

    active = az(["account", "show", "--query", "id", "--output", "tsv"])
    if active.returncode == 0 and active.stdout.strip() != subscription_id:
        print(f"warning: active Azure subscription is {active.stdout.strip()!r}, but tfvars uses {subscription_id!r}", file=sys.stderr)
        print(f"run: az account set --subscription {subscription_id}", file=sys.stderr)

    providers = az(
        [
            "provider",
            "list",
            "--subscription",
            subscription_id,
            "--query",
            "[].{namespace:namespace,registrationState:registrationState}",
            "--output",
            "json",
        ]
    )
    if providers.returncode != 0:
        print("warning: could not list Azure resource provider registrations", file=sys.stderr)
        print(providers.stderr.strip(), file=sys.stderr)
        print("Terraform will try to register the required providers.", file=sys.stderr)
        return 0

    try:
        provider_states = {
            item["namespace"]: item.get("registrationState", "")
            for item in json.loads(providers.stdout)
        }
    except json.JSONDecodeError:
        print("warning: could not parse Azure provider registration list", file=sys.stderr)
        print("Terraform will try to register the required providers.", file=sys.stderr)
        return 0

    missing = [
        namespace
        for namespace in REQUIRED_AZURE_RESOURCE_PROVIDERS
        if provider_states.get(namespace) != "Registered"
    ]
    if missing:
        print("warning: required Azure resource providers are not registered yet; Terraform will try to register them", file=sys.stderr)
        print("manual command:", file=sys.stderr)
        print(f"for ns in {' '.join(missing)}; do az provider register --namespace \"$ns\" --subscription {subscription_id}; done", file=sys.stderr)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cloud", nargs="?", choices=sorted(DEFAULTS))
    parser.add_argument("--force", action="store_true", help="overwrite existing terraform.tfvars.json")
    parser.add_argument("--skip-cloud-checks", action="store_true", help="skip cloud project/API validation")
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
    if not args.skip_cloud_checks:
        if cloud == "aws":
            status = validate_aws(values)
        elif cloud == "gcp":
            status = validate_gcp(values)
        else:
            status = validate_azure(values)
        if status != 0:
            return status

    output_path = EXAMPLES / cloud / "terraform.tfvars.json"
    if output_path.exists() and not args.force:
        if not ask_bool(f"{output_path} already exists. Overwrite it", False):
            print(f"left existing {output_path} unchanged", file=sys.stderr)
            print("rerun with --force to overwrite without prompting", file=sys.stderr)
            return 1

    output_path.write_text(json.dumps(values, indent=2) + "\n")
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
