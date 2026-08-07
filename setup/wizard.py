"""Pinecone BYOC setup wizard."""

import argparse
import contextlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

import yaml
from autocomplete import read_input_with_cycle, read_input_with_placeholder
from rich.console import Console
from rich.panel import Panel
from rich.status import Status

# pinecone blue
BLUE = "#002BFF"

PINECONE_VERSION = "main-94a9e90"

console = Console()


@dataclass
class PreflightResult:
    name: str
    passed: bool
    message: str
    details: str | None = None


# ---------------------------------------------------------------------------
# Resumable answer state
# ---------------------------------------------------------------------------


def _https_remote(url: str) -> str:
    # a clone URL can carry a token, and this one is written into a file the customer keeps
    url = url.strip().removesuffix(".git")
    if url.startswith("git@"):
        host, _, path = url[len("git@") :].partition(":")
        return f"https://{host}/{path}"
    if url.startswith("ssh://git@"):
        return "https://" + url[len("ssh://git@") :]
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    if "@" in rest.split("/", 1)[0]:
        rest = rest.split("@", 1)[1]
    return f"{scheme}://{rest}"


def module_pin(source_dir: str) -> tuple[str, str] | None:
    def git(*args: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", source_dir, *args], capture_output=True, text=True, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    remote = git("remote", "get-url", "origin")
    if not commit or not remote:
        return None
    url = _https_remote(remote)
    if not url.startswith("https://") or "@" in url:
        return None
    return url, commit


def pinned_rev(pyproject_path: str) -> str | None:
    if not os.path.isfile(pyproject_path):
        return None
    match = re.search(r'rev\s*=\s*"([0-9a-f]{7,40})"', open(pyproject_path).read())
    return match.group(1) if match else None


class WizardState:
    """Persists non-secret wizard answers to a JSON file in the project dir.

    A mid-wizard failure can then be resumed without re-typing everything:
    each answer is saved as it is entered and replayed as the prompt default
    on the next run. Secrets are never handed to this class (see BaseSetupWizard
    ._prompt, which skips persistence for password fields).
    """

    FILENAME = ".setup-state.json"

    def __init__(self, output_dir: str):
        self._path = os.path.join(output_dir, self.FILENAME)
        self._data: dict = {}

    def load(self) -> "WizardState":
        try:
            with open(self._path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._data = data
        except (OSError, ValueError):
            self._data = {}
        return self

    @property
    def is_empty(self) -> bool:
        return not self._data

    def stored_keys(self) -> list[str]:
        return list(self._data)

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass

    def unset(self, key: str) -> None:
        if key not in self._data:
            return
        del self._data[key]
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            os.remove(self._path)
        self._data = {}


# ---------------------------------------------------------------------------
# Base Setup Wizard (shared between AWS and GCP)
# ---------------------------------------------------------------------------


class BaseSetupWizard:
    TOTAL_STEPS = 13
    CLOUD_NAME: str = ""
    HEADER_TITLE: str = "Pinecone BYOC Setup Wizard"
    HEADER_SUBTITLE: str = "This wizard will set up everything you need to deploy Pinecone BYOC."
    DEFAULT_CIDR: str = "10.0.0.0/16"
    CIDR_DESC: str = "The IP range for your VPC (must not conflict with existing VPCs)"
    DELETION_PROTECTION_DESC: str = ""
    PRIVATE_ACCESS_DESC: str = ""
    METADATA_NAME: str = "tags"

    def __init__(
        self,
        headless: bool = False,
        stack_name: str = "prod",
        skip_install: bool = False,
        dev_source: str | None = None,
    ):
        self.results: list[PreflightResult] = []
        self._current_step = 0
        self._headless = headless
        self._stack_name = stack_name
        self._skip_install = skip_install
        # resumable answer state (created by _maybe_resume; None in headless)
        self._state: WizardState | None = None
        self._dev_source = dev_source

    def _step(self, title: str) -> str:
        self._current_step += 1
        return f"[{BLUE}]Step {self._current_step}/{self.TOTAL_STEPS}[/] · {title}"

    def _prompt(
        self,
        message: str,
        default: str | None = None,
        password: bool = False,
        key: str | None = None,
        options: list[str] | None = None,
    ) -> str:
        """Prompt for a value.

        When ``key`` is given, any previously saved answer for that key becomes
        the default (resume), and the entered value is persisted — unless this
        is a ``password`` field, whose value is never written to disk. When
        ``options`` is given, Tab cycles through them.
        """
        effective_default = default or ""
        if key and self._state is not None:
            effective_default = self._state.get(key, effective_default)

        if options:
            value = read_input_with_cycle(message, options, effective_default)
        else:
            value = read_input_with_placeholder(message, effective_default, password)

        if key and self._state is not None and not password:
            self._state.set(key, value)
        return value

    def _zone_default(self, key: str, available: list[str]) -> str:
        if self._state is not None:
            saved = self._state.get(key)
            if saved and not all(z.strip() in available for z in saved.split(",")):
                self._state.unset(key)
        return ",".join(available[:2])

    def _maybe_resume(self, output_dir: str) -> None:
        """Load prior answers and, if present, offer to resume."""
        self._state = WizardState(output_dir).load()
        saved_cloud = self._state.get("cloud")
        has_progress = any(k != "cloud" for k in self._state.stored_keys())

        if not has_progress:
            self._state.clear()
            self._state.set("cloud", self.CLOUD_NAME)
            return

        if saved_cloud not in ("", self.CLOUD_NAME):
            console.print()
            console.print(
                f"  [red]✗[/] Saved progress is for {saved_cloud} but you selected {self.CLOUD_NAME}"
            )
            console.print(
                f"  [dim]Re-run and pick {saved_cloud}, or delete {WizardState.FILENAME}[/]"
            )
            sys.exit(1)

        saved_region = self._state.get("region")
        summary = self.CLOUD_NAME + (f", region {saved_region}" if saved_region else "")
        console.print()
        console.print(
            f"  [yellow]![/] Found saved progress from a previous run [dim]({summary})[/]"
        )
        response = self._prompt("Resume previous setup? (Y/n)", "Y")
        if response.lower() not in ("y", "yes", ""):
            self._state.clear()
        self._state.set("cloud", self.CLOUD_NAME)

    CONTROL_PLANE_ENV = {
        "global-env": "PINECONE_GLOBAL_ENV",
        "api-url": "PINECONE_API_URL",
        "auth0-domain": "PINECONE_AUTH0_DOMAIN",
        "gcp-project": "PINECONE_GCP_PROJECT",
        "amp-aws-account-id": "PINECONE_AMP_AWS_ACCOUNT_ID",
    }
    CONTROL_PLANE_KEYS: tuple[str, ...] = ()

    def _control_plane_overrides(self) -> dict[str, str]:
        return {
            key: os.environ[self.CONTROL_PLANE_ENV[key]]
            for key in self.CONTROL_PLANE_KEYS
            if os.environ.get(self.CONTROL_PLANE_ENV[key])
        }

    def _write_main_py(self, output_dir: str, main_py: str) -> None:
        main_py = main_py.replace("__CONTROL_PLANE__\n", self._control_plane_block())
        with open(os.path.join(output_dir, "__main__.py"), "w") as f:
            f.write(main_py)
        console.print("  [green]✓[/] Created __main__.py")

    def _control_plane_config(self, project_name: str, control_plane: dict[str, str] | None) -> str:
        return "".join(
            f"  {project_name}:{key}: {value}\n" for key, value in (control_plane or {}).items()
        )

    def _control_plane_block(self) -> str:
        """The generated program reads these from stack config.

        A key the stack does not set is left out rather than passed as None, so it
        falls back to the installed component's default instead of overriding it.
        """
        reads = "".join(
            f'        "{key.replace("-", "_")}": config.get("{key}"),\n'
            for key in self.CONTROL_PLANE_KEYS
        )
        return (
            "control_plane = {\n"
            "    name: value\n"
            "    for name, value in {\n"
            f"{reads}"
            "    }.items()\n"
            "    if value is not None\n"
            "}\n"
        )

    def _write_pyproject(self, output_dir: str, cloud: str, dev_source: str | None) -> None:
        pyproject_content = (
            "[project]\n"
            'name = "pinecone-byoc"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.12"\n'
            f'dependencies = ["pulumi-pinecone-byoc[{cloud}]"]\n'
        )
        if dev_source:
            path = os.path.abspath(dev_source).replace("\\", "/")
            pyproject_content += (
                "\n[tool.uv.sources]\n"
                f'pulumi-pinecone-byoc = {{ path = "{path}", editable = true }}\n'
            )
        else:
            pin = module_pin(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if pin:
                url, commit = pin
                pyproject_content += (
                    "\n[tool.uv.sources]\n"
                    f'pulumi-pinecone-byoc = {{ git = "{url}", rev = "{commit}" }}\n'
                )
            else:
                console.print(
                    "  [yellow]⚠[/] Could not pin the module version: this project will "
                    "install whatever is newest at sync time"
                )
        pyproject_path = os.path.join(output_dir, "pyproject.toml")
        with open(pyproject_path, "w") as f:
            f.write(pyproject_content)
        console.print("  [green]✓[/] Created pyproject.toml")

    def _print_header(self):
        console.print()
        console.print(
            Panel.fit(
                f"[bold {BLUE}]{self.HEADER_TITLE}[/]",
                border_style=BLUE,
                padding=(0, 2),
            )
        )
        console.print()
        console.print(f"  {self.HEADER_SUBTITLE}", style="dim")
        console.print()

    def _get_api_key(self) -> str | None:
        console.print()
        console.print(f"  {self._step('Pinecone API Key')}")
        console.print("  [dim]Find your key at app.pinecone.io[/]")
        console.print()

        env_key = os.environ.get("PINECONE_API_KEY")
        if env_key:
            use_env = self._prompt("Found PINECONE_API_KEY in environment. Use it? (Y/n)", "Y")
            if use_env.lower() in ("y", "yes", ""):
                return env_key

        api_key = self._prompt("Enter your Pinecone API key", password=True)
        if not api_key:
            console.print("\n  [red]✗[/] API key is required")
            return None

        return api_key

    def _validate_api_key(self, api_key: str) -> bool:
        console.print()
        console.print(f"  {self._step('Validating API Key')}")
        console.print()

        with Status("  [dim]Checking API key...[/]", console=console, spinner="dots"):
            try:
                req = urllib.request.Request(
                    "https://api.pinecone.io/indexes",
                    headers={"Api-Key": api_key},
                )
                with urllib.request.urlopen(req, timeout=10) as response:
                    response.read()

                console.print("  [green]✓[/] API key is valid")
                return True
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    console.print("  [red]✗[/] Invalid API key")
                else:
                    console.print(f"  [red]✗[/] API error: {e.code}")
                return False
            except Exception as e:
                console.print(f"  [red]✗[/] Failed to validate API key: {e}")
                return False

    def _get_cidr(self) -> str:
        console.print()
        console.print(f"  {self._step('VPC CIDR Block')}")
        console.print(f"  [dim]{self.CIDR_DESC}[/]")
        console.print()
        return self._prompt("Enter CIDR block", self.DEFAULT_CIDR, key="cidr")

    def _get_deletion_protection(self) -> bool:
        console.print()
        console.print(f"  {self._step('Deletion Protection')}")
        console.print(f"  [dim]{self.DELETION_PROTECTION_DESC}[/]")
        console.print()
        response = self._prompt("Enable deletion protection? (Y/n)", "Y", key="deletion_protection")
        return response.lower() in ("y", "yes", "")

    def _get_public_access(self) -> bool:
        console.print()
        console.print(f"  {self._step('Network Access')}")
        console.print("  [dim]Public access allows connections from the internet[/]")
        console.print(f"  [dim]{self.PRIVATE_ACCESS_DESC}[/]")
        console.print()
        response = self._prompt("Enable public access? (Y/n)", "Y", key="public_access")
        return response.lower() in ("y", "yes", "")

    def _get_custom_metadata(self) -> dict[str, str]:
        name = self.METADATA_NAME
        console.print()
        console.print(f"  {self._step(f'Resource {name.title()}')}")
        console.print(
            f"  [dim]Add custom {name} to all {self.CLOUD_NAME} resources (for cost tracking, etc.)[/]"
        )
        console.print("  [dim]Format: key=value, comma-separated (e.g., team=platform,env=prod)[/]")
        console.print()

        input_val = self._prompt(f"Enter {name} (or press Enter to skip)", "", key=f"{name}_input")
        if not input_val:
            return {}

        metadata = {}
        for pair in input_val.split(","):
            pair = pair.strip()
            if "=" in pair:
                key, value = pair.split("=", 1)
                metadata[key.strip()] = value.strip()

        if metadata:
            console.print(f"  [dim]{name.title()} to apply: {metadata}[/]")

        return metadata

    def _get_project_name(self) -> str:
        console.print()
        console.print(f"  {self._step('Project Name')}")
        console.print("  [dim]A short name for this deployment (e.g., 'pinecone-prod')[/]")
        console.print()
        return self._prompt("Enter project name", "pinecone-byoc", key="project_name")

    def _setup_pulumi_backend(self) -> bool:
        console.print()
        console.print(f"  {self._step('Pulumi Backend')}")
        console.print("  [dim]Where to store infrastructure state[/]")
        console.print()

        backend = self._prompt("Backend (local/cloud)", "local", key="backend").lower()
        use_local = backend != "cloud"

        if use_local:
            console.print()
            console.print("  [dim]Enter a passphrase to encrypt secrets (remember this!)[/]")
            passphrase = self._prompt("Passphrase", password=True)
            if not passphrase:
                console.print("  [red]✗[/] Passphrase is required for local backend")
                return False

            os.environ["PULUMI_CONFIG_PASSPHRASE"] = passphrase

            result = subprocess.run(
                ["pulumi", "login", "--local"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                console.print("  [green]✓[/] Using local backend (~/.pulumi)")
            else:
                console.print(
                    f"  [red]✗[/] Failed to set up local backend: {result.stderr.strip()}"
                )
                return False
        else:
            # check if already logged in to cloud
            result = subprocess.run(
                ["pulumi", "whoami"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print("  [yellow]![/] Not logged in to Pulumi Cloud")
                console.print("  [dim]Run:[/] pulumi login")
                return False
            console.print(f"  [green]✓[/] Using Pulumi Cloud ({result.stdout.strip()})")

        return True

    def _check_pulumi_installed(self) -> bool:
        return shutil.which("pulumi") is not None

    def _print_success(self, output_dir: str):
        # setup finished successfully — drop the resume checkpoint
        if self._state is not None:
            self._state.clear()
        console.print()
        console.print(
            Panel.fit(
                "[bold green]Setup Complete![/]",
                border_style="green",
                padding=(0, 2),
            )
        )
        console.print()
        dir_name = os.path.basename(os.path.abspath(output_dir))
        console.print("  [dim]To deploy, run:[/]")
        console.print(f"    [bold {BLUE}]pulumi -C {dir_name} up[/]")
        console.print()


# ---------------------------------------------------------------------------
# AWS Setup Wizard
# ---------------------------------------------------------------------------


class AWSPreflightChecker:
    def __init__(self, region: str, azs: list[str], cidr: str):
        import boto3

        self.region = region
        self.azs = azs
        self.cidr = cidr
        self.results: list[PreflightResult] = []

        self.ec2 = boto3.client("ec2", region_name=region)
        self.eks = boto3.client("eks", region_name=region)
        self.servicequotas = boto3.client("service-quotas", region_name=region)

    def run_checks(self) -> bool:
        checks = [
            ("VPC Quota", self._check_vpc_quota),
            ("Elastic IPs", self._check_eip_quota),
            ("NAT Gateways", self._check_nat_gateway_quota),
            ("Internet Gateways", self._check_igw_quota),
            ("EKS Clusters", self._check_eks_cluster_quota),
            ("Network Load Balancers", self._check_nlb_quota),
            ("Availability Zones", self._check_az_availability),
            ("Instance Types", self._check_instance_types),
            ("VPC CIDR", self._check_cidr_conflicts),
        ]

        for name, check_fn in checks:
            with Status(f"  [dim]Checking {name}...[/]", console=console, spinner="dots"):
                check_fn()

            # print the result that was just added
            r = self.results[-1]
            status = "✓" if r.passed else "✗"
            color = "green" if r.passed else "red"
            console.print(f"  [{color}]{status}[/] {r.name}: {r.message}")
            if r.details and not r.passed:
                console.print(f"    [dim]{r.details}[/]")

        failed = [r for r in self.results if not r.passed]
        return len(failed) == 0

    def _add_result(self, name: str, passed: bool, message: str, details: str | None = None):
        result = PreflightResult(name, passed, message, details)
        self.results.append(result)

    def _get_quota(self, service_code: str, quota_code: str) -> float | None:
        try:
            response = self.servicequotas.get_service_quota(
                ServiceCode=service_code, QuotaCode=quota_code
            )
            return response["Quota"]["Value"]
        except Exception:
            try:
                response = self.servicequotas.get_aws_default_service_quota(
                    ServiceCode=service_code, QuotaCode=quota_code
                )
                return response["Quota"]["Value"]
            except Exception:
                return None

    def _check_vpc_quota(self):
        quota = self._get_quota("vpc", "L-F678F1CE") or 5
        try:
            vpcs = self.ec2.describe_vpcs()
            current = len(vpcs["Vpcs"])
            available = int(quota) - current

            self._add_result(
                "VPC Quota",
                available >= 1,
                f"{available} available [dim](using {current}/{int(quota)})[/]",
                "Request a quota increase via AWS Service Quotas" if available < 1 else None,
            )
        except Exception as e:
            self._add_result("VPC Quota", False, "Failed to check", str(e))

    def _check_eip_quota(self):
        needed = len(self.azs)  # one per AZ for NAT gateways
        quota = self._get_quota("ec2", "L-0263D0A3") or 5
        try:
            addresses = self.ec2.describe_addresses()
            current = len(addresses["Addresses"])
            available = int(quota) - current

            self._add_result(
                "Elastic IPs",
                available >= needed,
                f"{available} available, need {needed}",
                "Request quota increase for 'EC2-VPC Elastic IPs'" if available < needed else None,
            )
        except Exception as e:
            self._add_result("Elastic IPs", False, "Failed to check", str(e))

    def _check_nat_gateway_quota(self):
        quota = self._get_quota("vpc", "L-FE5A380F") or 5
        try:
            response = self.ec2.describe_nat_gateways(
                Filters=[{"Name": "state", "Values": ["available", "pending"]}]
            )

            # count NAT gateways per AZ (quota is per-AZ, not per-account)
            nat_gateways_by_az = {}
            for nat_gw in response["NatGateways"]:
                # get subnet AZ for this NAT gateway
                subnet_id = nat_gw.get("SubnetId")
                if subnet_id:
                    subnet_response = self.ec2.describe_subnets(SubnetIds=[subnet_id])
                    if subnet_response["Subnets"]:
                        az = subnet_response["Subnets"][0]["AvailabilityZone"]
                        nat_gateways_by_az[az] = nat_gateways_by_az.get(az, 0) + 1

            # check each requested AZ has capacity
            insufficient_azs = []
            for az in self.azs:
                current_in_az = nat_gateways_by_az.get(az, 0)
                available_in_az = int(quota) - current_in_az
                if available_in_az < 1:
                    insufficient_azs.append(f"{az} ({current_in_az}/{int(quota)})")

            if insufficient_azs:
                self._add_result(
                    "NAT Gateways",
                    False,
                    f"Insufficient capacity in: {', '.join(insufficient_azs)}",
                    "Request quota increase for 'NAT gateways per AZ'",
                )
            else:
                self._add_result(
                    "NAT Gateways",
                    True,
                    f"All AZs have capacity [dim](quota: {int(quota)} per AZ)[/]",
                )
        except Exception as e:
            self._add_result("NAT Gateways", False, "Failed to check", str(e))

    def _check_igw_quota(self):
        quota = self._get_quota("vpc", "L-A4707A72") or 5
        try:
            response = self.ec2.describe_internet_gateways()
            current = len(response["InternetGateways"])
            available = int(quota) - current

            self._add_result(
                "Internet Gateways",
                available >= 1,
                f"{available} available",
                "Request quota increase for 'Internet gateways per Region'"
                if available < 1
                else None,
            )
        except Exception as e:
            self._add_result("Internet Gateways", False, "Failed to check", str(e))

    def _check_nlb_quota(self):
        import boto3

        quota = self._get_quota("elasticloadbalancing", "L-53DA6B97") or 50
        try:
            elb = boto3.client("elbv2", region_name=self.region)
            response = elb.describe_load_balancers()
            nlbs = [lb for lb in response["LoadBalancers"] if lb["Type"] == "network"]
            current = len(nlbs)
            available = int(quota) - current

            self._add_result(
                "Network Load Balancers",
                available >= 1,
                f"{available} available",
                "Request quota increase for 'Network Load Balancers'" if available < 1 else None,
            )
        except Exception as e:
            self._add_result("Network Load Balancers", False, "Failed to check", str(e))

    def _check_eks_cluster_quota(self):
        quota = self._get_quota("eks", "L-1194D53C") or 100
        try:
            clusters = self.eks.list_clusters()
            current = len(clusters["clusters"])
            available = int(quota) - current

            self._add_result(
                "EKS Cluster Quota",
                available >= 1,
                f"{available} available [dim](using {current}/{int(quota)})[/]",
                "Request quota increase for 'Clusters'" if available < 1 else None,
            )
        except Exception as e:
            self._add_result("EKS Cluster Quota", False, "Failed to check", str(e))

    def _check_az_availability(self):
        try:
            azs_response = self.ec2.describe_availability_zones(
                Filters=[{"Name": "state", "Values": ["available"]}]
            )
            available_azs = [az["ZoneName"] for az in azs_response["AvailabilityZones"]]

            missing = [az for az in self.azs if az not in available_azs]
            self._add_result(
                "Availability Zones",
                len(missing) == 0,
                "All requested AZs available"
                if not missing
                else f"AZs not available: {', '.join(missing)}",
                f"Available AZs: {', '.join(available_azs)}" if missing else None,
            )
        except Exception as e:
            self._add_result("Availability Zones", False, "Failed to check", str(e))

    def _check_instance_types(self):
        # check all instance types needed for the cluster
        instance_types = ["m6idn.large", "i7ie.large", "m6idn.xlarge", "r6in.large"]
        all_available = True
        unavailable = []

        try:
            for instance_type in instance_types:
                response = self.ec2.describe_instance_type_offerings(
                    LocationType="availability-zone",
                    Filters=[
                        {"Name": "instance-type", "Values": [instance_type]},
                        {"Name": "location", "Values": self.azs},
                    ],
                )
                offered_azs = [o["Location"] for o in response["InstanceTypeOfferings"]]
                missing = [az for az in self.azs if az not in offered_azs]
                if missing:
                    all_available = False
                    unavailable.append(f"{instance_type}")

            self._add_result(
                "Instance Types",
                all_available,
                "All required types available"
                if all_available
                else f"Unavailable: {', '.join(unavailable)}",
                "Choose different AZs or request capacity" if not all_available else None,
            )
        except Exception as e:
            self._add_result("Instance Types", False, "Failed to check", str(e))

    def _check_cidr_conflicts(self):
        try:
            target_net = ipaddress.ip_network(self.cidr)
        except ValueError:
            self._add_result(
                "VPC CIDR",
                False,
                f"Invalid CIDR: {self.cidr}",
                "Enter a valid CIDR block (e.g., 10.0.0.0/16)",
            )
            return

        # must be /16 for subnet calculation
        if target_net.prefixlen != 16:
            self._add_result(
                "VPC CIDR",
                False,
                f"CIDR must be a /16 (got /{target_net.prefixlen})",
                "Subnet calculation requires a /16 network (e.g., 10.0.0.0/16)",
            )
            return

        # must be RFC 1918 private range (AWS rejects CIDRs in 100.64.0.0/10 and other reserved ranges)
        rfc1918_ranges = [
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        ]
        if not any(target_net.subnet_of(r) for r in rfc1918_ranges):
            self._add_result(
                "VPC CIDR",
                False,
                f"{self.cidr} is not in an RFC 1918 private range",
                "Use a /16 block like 10.0.0.0/16, 172.16.0.0/16, or 192.168.0.0/16. "
                "See https://docs.aws.amazon.com/vpc/latest/userguide/vpc-cidr-blocks.html",
            )
            return

        # check overlap with existing VPCs
        try:
            response = self.ec2.describe_vpcs()
            conflicts = []
            for vpc in response["Vpcs"]:
                vpc_cidr = vpc.get("CidrBlock", "")
                if not vpc_cidr:
                    continue
                try:
                    existing_net = ipaddress.ip_network(vpc_cidr)
                    if target_net.overlaps(existing_net):
                        conflicts.append(vpc_cidr)
                except ValueError:
                    continue

            self._add_result(
                "VPC CIDR",
                len(conflicts) == 0,
                f"{self.cidr} available"
                if not conflicts
                else f"Conflicts with: {', '.join(conflicts)}",
                "Choose a different CIDR range to avoid conflicts" if conflicts else None,
            )
        except Exception as e:
            self._add_result("VPC CIDR", False, "Failed to check", str(e))


class AWSSetupWizard(BaseSetupWizard):
    CONTROL_PLANE_KEYS = ("global-env", "api-url", "auth0-domain", "gcp-project")
    TOTAL_STEPS = 15
    HEADER_TITLE = "Pinecone BYOC Setup Wizard"
    HEADER_SUBTITLE = "This wizard will set up everything you need to deploy Pinecone BYOC."
    DEFAULT_CIDR = "10.0.0.0/16"
    CIDR_DESC = "The IP range for your VPC (/16 from an RFC 1918 private range, must not conflict with existing VPCs)"
    DELETION_PROTECTION_DESC = "Protect RDS databases and S3 buckets from accidental deletion"
    PRIVATE_ACCESS_DESC = "Private access requires AWS PrivateLink (more secure)"
    METADATA_NAME = "tags"
    CLOUD_NAME = "AWS"

    def run(self, output_dir: str = ".") -> bool:
        if self._headless:
            return self._run_headless(output_dir)

        self._print_header()
        self._maybe_resume(output_dir)

        api_key = self._get_api_key()
        if not api_key:
            return False

        if not self._validate_api_key(api_key):
            return False

        if not self._validate_aws_creds():
            return False

        region = self._get_region()
        azs = self._get_azs(region)
        custom_ami_id = self._get_custom_ami_id()
        kms_key_arn = self._get_kms_key_arn()
        cidr = self._get_cidr()
        deletion_protection = self._get_deletion_protection()
        public_access = self._get_public_access()
        tags = self._get_custom_metadata()

        if not self._run_preflight_checks(region, azs, cidr):
            return False

        project_name = self._get_project_name()

        if not self._setup_pulumi_backend():
            return False

        return self._generate_project(
            output_dir,
            project_name,
            api_key,
            region,
            azs,
            cidr,
            deletion_protection,
            public_access,
            tags,
            custom_ami_id=custom_ami_id,
            kms_key_arn=kms_key_arn,
        )

    def _run_headless(self, output_dir: str) -> bool:
        console.print("  [dim]Running in headless mode (reading from environment)[/]")

        api_key = os.environ.get("PINECONE_API_KEY")
        if not api_key:
            console.print("  [red]✗[/] PINECONE_API_KEY environment variable is required")
            return False

        region = os.environ.get("PINECONE_REGION", "us-east-1")
        azs_str = os.environ.get("PINECONE_AZS", f"{region}a,{region}b")
        azs = [az.strip() for az in azs_str.split(",")]
        cidr = os.environ.get("PINECONE_VPC_CIDR", self.DEFAULT_CIDR)
        deletion_protection = (
            os.environ.get("PINECONE_DELETION_PROTECTION", "true").lower() == "true"
        )
        public_access = os.environ.get("PINECONE_PUBLIC_ACCESS", "true").lower() == "true"
        project_name = os.environ.get("PINECONE_PROJECT_NAME", "pinecone-byoc")
        custom_ami_id = os.environ.get("PINECONE_CUSTOM_AMI_ID", "") or None
        kms_key_arn = os.environ.get("PINECONE_KMS_KEY_ARN", "") or None
        control_plane = self._control_plane_overrides()

        return self._generate_project(
            output_dir,
            project_name,
            api_key,
            region,
            azs,
            cidr,
            deletion_protection,
            public_access,
            {},
            custom_ami_id=custom_ami_id,
            kms_key_arn=kms_key_arn,
            control_plane=control_plane,
        )

    def _select_aws_profile(self) -> None:
        import boto3

        if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
            console.print(
                "  [dim]Using credentials from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY[/]"
            )
            return

        available = boto3.Session().available_profiles
        default = os.environ.get("AWS_PROFILE") or "default"
        options = [default] + [p for p in available if p != default]

        console.print("  [dim]Tab cycles through configured profiles; Enter to accept[/]")
        profile = self._prompt("AWS profile", default, key="aws_profile", options=options)
        console.print()

        if profile:
            os.environ["AWS_PROFILE"] = profile
            with contextlib.suppress(Exception):
                boto3.setup_default_session(profile_name=profile)

    def _validate_aws_creds(self) -> bool:
        console.print()
        console.print(f"  {self._step('AWS Credentials')}")
        console.print()

        self._select_aws_profile()

        with Status("  [dim]Validating AWS credentials...[/]", console=console, spinner="dots"):
            try:
                import boto3

                session = boto3.Session()
                sts = session.client("sts")
                identity = sts.get_caller_identity()
                account_id = identity["Account"]
                profile = session.profile_name or "default"
            except Exception as e:
                console.print(f"  [red]✗[/] AWS credentials invalid: {e}")
                console.print()
                console.print("  [dim]Make sure you have valid AWS credentials configured.[/]")
                console.print("  [dim]You can set them via:[/]")
                console.print("    [dim]· AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY[/]")
                console.print("    [dim]· aws configure[/]")
                console.print("    [dim]· AWS_PROFILE environment variable[/]")
                return False

        console.print(
            f"  [green]✓[/] AWS credentials valid [dim](Account: {account_id}, Profile: {profile})[/]"
        )
        return True

    def _get_region(self) -> str:
        console.print()
        console.print(f"  {self._step('AWS Region')}")
        console.print()
        return self._prompt("Enter AWS region", "us-east-1", key="region")

    def _fetch_azs(self, region: str) -> list[str]:
        import boto3

        try:
            ec2 = boto3.client("ec2", region_name=region)
            response = ec2.describe_availability_zones(
                Filters=[{"Name": "state", "Values": ["available"]}]
            )
            return sorted([az["ZoneName"] for az in response["AvailabilityZones"]])
        except Exception as e:
            console.print(f"  [yellow]⚠[/] Could not fetch AZs from AWS: {e}")
            return [f"{region}a", f"{region}b", f"{region}c"]

    def _get_azs(self, region: str) -> list[str]:
        console.print()
        console.print(f"  {self._step('Availability Zones')}")
        console.print()

        with Status("  [dim]Fetching availability zones...[/]", console=console, spinner="dots"):
            available = self._fetch_azs(region)

        console.print(f"  [dim]Available in {region}:[/] {', '.join(available)}")

        azs_input = self._prompt(
            "Enter AZs (comma-separated)", self._zone_default("azs", available), key="azs"
        )
        azs = [az.strip() for az in azs_input.split(",")]
        return azs

    def _get_custom_ami_id(self) -> str | None:
        console.print()
        console.print(f"  {self._step('Custom AMI (Optional)')}")
        console.print(
            "  [dim]Specify a custom AMI ID for EKS nodes (leave blank for default AWS AMI)[/]"
        )
        console.print()
        ami_id = self._prompt("Enter AMI ID (or press Enter to skip)", "", key="custom_ami_id")
        return ami_id or None

    def _get_kms_key_arn(self) -> str | None:
        console.print()
        console.print(f"  {self._step('KMS Key (Optional)')}")
        console.print(
            "  [dim]Provide a KMS key ARN to encrypt S3 buckets and RDS with your own key.[/]"
        )
        console.print(
            "  [dim]Leave blank to use default AWS-managed encryption (AES256/default RDS key).[/]"
        )
        console.print()
        arn = self._prompt("Enter KMS key ARN (or press Enter to skip)", "", key="kms_key_arn")
        return arn or None

    def _run_preflight_checks(self, region: str, azs: list[str], cidr: str) -> bool:
        console.print()
        console.print(f"  {self._step('Preflight Checks')}")
        console.print()

        checker = AWSPreflightChecker(region, azs, cidr)
        if not checker.run_checks():
            console.print()
            console.print(
                "  [red]Preflight checks failed. Fix the issues above before proceeding.[/]"
            )
            return False

        return True

    def _generate_project(
        self,
        output_dir: str,
        project_name: str,
        api_key: str,
        region: str,
        azs: list[str],
        cidr: str,
        deletion_protection: bool,
        public_access: bool,
        tags: dict[str, str],
        custom_ami_id: str | None = None,
        kms_key_arn: str | None = None,
        control_plane: dict[str, str] | None = None,
    ):
        console.print()

        console.print(f"  {self._step('Creating Project')}")
        console.print()

        if not self._check_pulumi_installed():
            console.print("  [red]✗[/] Pulumi CLI not found")
            console.print("  [dim]Install Pulumi first:[/] https://www.pulumi.com/docs/install/")
            return False

        pulumi_yaml = {
            "name": project_name,
            "runtime": {
                "name": "python",
                "options": {"virtualenv": ".venv", "toolchain": "uv"},
            },
            "description": "Pinecone BYOC deployment",
        }

        os.makedirs(output_dir, exist_ok=True)
        pulumi_yaml_path = os.path.join(output_dir, "Pulumi.yaml")
        with open(pulumi_yaml_path, "w") as f:
            yaml.dump(pulumi_yaml, f, default_flow_style=False)
        console.print("  [green]✓[/] Created Pulumi.yaml")

        # create __main__.py
        main_py = '''"""Pinecone BYOC deployment (AWS)."""

import pulumi
from pulumi_pinecone_byoc.aws import PineconeAWSCluster, PineconeAWSClusterArgs

config = pulumi.Config()

__CONTROL_PLANE__
cluster = PineconeAWSCluster(
    name="pinecone-aws-cluster",
    args=PineconeAWSClusterArgs(
        pinecone_api_key=config.require_secret("pinecone-api-key"),
        pinecone_version=config.require("pinecone-version"),
        region=config.require("region"),
        vpc_cidr=config.get("vpc-cidr"),
        availability_zones=config.require_object("availability-zones"),
        deletion_protection=config.get_bool("deletion-protection") if config.get_bool("deletion-protection") is not None else True,
        public_access_enabled=config.get_bool("public-access-enabled") if config.get_bool("public-access-enabled") is not None else True,
        custom_ami_id=config.get("custom-ami-id"),
        kms_key_arn=config.get("kms-key-arn"),
        tags=config.get_object("tags"),
        **control_plane,
    ),
)

update_kubeconfig_command = cluster.name.apply(
    lambda name: f"aws eks update-kubeconfig --region {config.require('region')} --name {name}"
)
pulumi.export("environment", cluster.environment_name)
pulumi.export("update_kubeconfig_command", update_kubeconfig_command)
if config.get_bool("public-access-enabled") is False:
    pulumi.export("vpc_endpoint_service_name", cluster.vpc_endpoint_service_name)
'''

        self._write_main_py(output_dir, main_py)

        self._write_pyproject(output_dir, "aws", self._dev_source)

        # create stack config
        stack_name = self._stack_name
        deletion_protection_str = str(deletion_protection).lower()
        public_access_str = str(public_access).lower()
        config_content = f"""config:
  aws:region: {region}
  {project_name}:region: {region}
  {project_name}:pinecone-version: {PINECONE_VERSION}
  {project_name}:vpc-cidr: {cidr}
  {project_name}:deletion-protection: {deletion_protection_str}
  {project_name}:public-access-enabled: {public_access_str}
  {project_name}:availability-zones:
"""
        for az in azs:
            config_content += f"    - {az}\n"

        config_content += self._control_plane_config(project_name, control_plane)

        # add custom AMI ID if provided
        if custom_ami_id:
            config_content += f"  {project_name}:custom-ami-id: {custom_ami_id}\n"

        # add customer KMS key ARN if provided
        if kms_key_arn:
            config_content += f"  {project_name}:kms-key-arn: {kms_key_arn}\n"

        # add tags if provided (quote values to handle YAML special chars)
        if tags:
            config_content += f"  {project_name}:tags:\n"
            for key, value in tags.items():
                config_content += f'    {key}: "{value}"\n'

        config_path = os.path.join(output_dir, f"Pulumi.{stack_name}.yaml")
        with open(config_path, "w") as f:
            f.write(config_content)
        console.print(f"  [green]✓[/] Created Pulumi.{stack_name}.yaml")

        if self._skip_install:
            return True

        # install dependencies with uv
        with Status("  [dim]Installing dependencies...[/]", console=console, spinner="dots"):
            result = subprocess.run(
                ["uv", "sync"],
                cwd=output_dir,
                capture_output=True,
                text=True,
            )

        if result.returncode == 0:
            # get installed version
            version_result = subprocess.run(
                ["uv", "pip", "show", "pulumi-pinecone-byoc"],
                cwd=output_dir,
                capture_output=True,
                text=True,
            )
            pkg_version = "unknown"
            for line in version_result.stdout.splitlines():
                if line.startswith("Version:"):
                    pkg_version = line.split(":", 1)[1].strip()
                    break
            console.print(
                f"  [green]✓[/] Dependencies installed [dim](pulumi-pinecone-byoc v{pkg_version})[/]"
            )
        else:
            console.print(f"  [red]✗[/] Failed to install dependencies: {result.stderr.strip()}")
            console.print("  [dim]Run manually:[/] uv sync")
            return False

        # init stack
        with Status("  [dim]Initializing stack...[/]", console=console, spinner="dots"):
            result = subprocess.run(
                [
                    "pulumi",
                    "stack",
                    "select",
                    "--create",
                    stack_name,
                    "--cwd",
                    output_dir,
                ],
                capture_output=True,
                text=True,
            )

        if result.returncode == 0:
            console.print(f"  [green]✓[/] Stack {stack_name} ready")
        else:
            console.print(f"  [yellow]⚠[/] Stack init: {result.stderr.strip()}")

        # set api key as secret
        with Status("  [dim]Storing API key securely...[/]", console=console, spinner="dots"):
            result = subprocess.run(
                [
                    "pulumi",
                    "config",
                    "set",
                    "--secret",
                    "pinecone-api-key",
                    api_key,
                    "--stack",
                    stack_name,
                    "--cwd",
                    output_dir,
                ],
                capture_output=True,
                text=True,
            )

        if result.returncode != 0:
            console.print(f"  [red]✗[/] Failed to store API key: {result.stderr.strip()}")
            console.print(
                "  [dim]Run manually:[/] pulumi config set --secret pinecone-api-key <key>"
            )
            return False

        console.print("  [green]✓[/] API key stored securely")

        self._print_success(output_dir)
        return True


# ---------------------------------------------------------------------------
# GCP Setup Wizard
# ---------------------------------------------------------------------------


class GCPPreflightChecker:
    def __init__(self, project_id: str, region: str, zones: list[str], cidr: str):
        self.project_id = project_id
        self.region = region
        self.zones = zones
        self.cidr = cidr
        self.results: list[PreflightResult] = []

    def run_checks(self) -> bool:
        checks = [
            ("GCP APIs", self._check_apis_enabled),
            ("VPC Networks", self._check_vpc_quota),
            ("External IPs", self._check_external_ip_quota),
            ("GKE Clusters", self._check_gke_quota),
            ("Machine Types", self._check_machine_types),
            ("Availability Zones", self._check_zones),
            ("VPC CIDR", self._check_cidr_conflicts),
        ]

        for name, check_fn in checks:
            with Status(f"  [dim]Checking {name}...[/]", console=console, spinner="dots"):
                check_fn()

            # print the result that was just added
            r = self.results[-1]
            status = "✓" if r.passed else "✗"
            color = "green" if r.passed else "red"
            console.print(f"  [{color}]{status}[/] {r.name}: {r.message}")
            if r.details and not r.passed:
                console.print(f"    [dim]{r.details}[/]")

        failed = [r for r in self.results if not r.passed]
        return len(failed) == 0

    def _add_result(self, name: str, passed: bool, message: str, details: str | None = None):
        result = PreflightResult(name, passed, message, details)
        self.results.append(result)

    def _gcloud_json(self, args: list[str]):
        result = subprocess.run(
            ["gcloud"] + args + [f"--project={self.project_id}", "--format=json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip().split("\n")[0])

        return json.loads(result.stdout)

    def _check_apis_enabled(self):
        required_apis = [
            "alloydb.googleapis.com",
            "autoscaling.googleapis.com",
            "cloudapis.googleapis.com",
            "cloudkms.googleapis.com",
            "cloudresourcemanager.googleapis.com",
            "compute.googleapis.com",
            "container.googleapis.com",
            "dns.googleapis.com",
            "domains.googleapis.com",
            "iam.googleapis.com",
            "iamcredentials.googleapis.com",
            "networkmanagement.googleapis.com",
            "secretmanager.googleapis.com",
            "servicedirectory.googleapis.com",
            "servicemanagement.googleapis.com",
            "servicenetworking.googleapis.com",
            "siteverification.googleapis.com",
            "storage.googleapis.com",
        ]

        try:
            result = subprocess.run(
                [
                    "gcloud",
                    "services",
                    "list",
                    "--enabled",
                    "--format=value(config.name)",
                    f"--project={self.project_id}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                self._add_result(
                    "GCP APIs",
                    False,
                    f"Failed: {result.stderr.strip().split(chr(10))[0]}",
                )
                return

            enabled_apis = result.stdout.strip().split("\n")
            missing = [api for api in required_apis if api not in enabled_apis]

            if missing:
                short_names = [api.replace(".googleapis.com", "") for api in missing]
                self._add_result(
                    "GCP APIs",
                    False,
                    f"{len(missing)} missing: {', '.join(short_names)}",
                    f"Run: gcloud services enable {' '.join(missing)} --project={self.project_id}",
                )
            else:
                self._add_result(
                    "GCP APIs", True, f"All {len(required_apis)} required APIs enabled"
                )
        except Exception as e:
            self._add_result("GCP APIs", False, f"Failed to check: {e}")

    def _check_vpc_quota(self):
        try:
            networks = self._gcloud_json(["compute", "networks", "list"])
            current = len(networks) if isinstance(networks, list) else 0
            quota = 15
            available = quota - current
            self._add_result(
                "VPC Networks",
                available >= 1,
                f"{available} available [dim](using {current}/{quota})[/]",
                "Request quota increase for 'VPC networks'" if available < 1 else None,
            )
        except Exception as e:
            self._add_result("VPC Networks", False, f"Failed to check: {e}")

    def _check_external_ip_quota(self):
        needed = 1  # one for external ingress
        try:
            addresses = self._gcloud_json(
                ["compute", "addresses", "list", f"--regions={self.region}"]
            )
            current = len(addresses) if isinstance(addresses, list) else 0
            quota = 8  # default regional static IP quota
            available = quota - current
            self._add_result(
                "External IPs",
                available >= needed,
                f"{available} available, need {needed} [dim](using {current}/{quota})[/]",
                "Request quota increase for 'Static IP addresses'" if available < needed else None,
            )
        except Exception as e:
            self._add_result("External IPs", False, f"Failed to check: {e}")

    def _check_gke_quota(self):
        try:
            data = self._gcloud_json(["container", "clusters", "list"])
            current = len(data) if isinstance(data, list) else 0
            quota = 50
            available = quota - current
            self._add_result(
                "GKE Clusters",
                available >= 1,
                f"{available} available [dim](using {current}/{quota})[/]",
            )
        except Exception as e:
            self._add_result("GKE Clusters", False, f"Failed to check: {e}")

    def _check_machine_types(self):
        machine_types = ["n2-standard-4", "n2-standard-2", "n2-highmem-2"]
        unavailable = []

        try:
            for zone in self.zones:
                result = subprocess.run(
                    [
                        "gcloud",
                        "compute",
                        "machine-types",
                        "list",
                        f"--project={self.project_id}",
                        f"--zones={zone}",
                        "--format=value(name)",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    self._add_result(
                        "Machine Types",
                        False,
                        f"Failed: {result.stderr.strip().split(chr(10))[0]}",
                    )
                    return

                available = result.stdout.strip().split("\n")
                for mt in machine_types:
                    if mt not in available:
                        unavailable.append(f"{mt} in {zone}")

            self._add_result(
                "Machine Types",
                len(unavailable) == 0,
                "All required types available"
                if not unavailable
                else f"Unavailable: {', '.join(unavailable)}",
                "Choose different zones or machine types" if unavailable else None,
            )
        except Exception as e:
            self._add_result("Machine Types", False, f"Failed to check: {e}")

    def _check_zones(self):
        try:
            result = subprocess.run(
                [
                    "gcloud",
                    "compute",
                    "zones",
                    "list",
                    "--format=value(name)",
                    f"--project={self.project_id}",
                    f"--filter=region:{self.region}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                self._add_result(
                    "Availability Zones",
                    False,
                    f"Failed: {result.stderr.strip().split(chr(10))[0]}",
                )
                return

            available_zones = [z for z in result.stdout.strip().split("\n") if z]
            invalid = [zone for zone in self.zones if zone not in available_zones]

            if invalid:
                self._add_result(
                    "Availability Zones",
                    False,
                    f"Invalid zones: {', '.join(invalid)}",
                    f"Valid zones for {self.region}: {', '.join(available_zones)}",
                )
            else:
                self._add_result("Availability Zones", True, "All requested zones available")
        except Exception as e:
            self._add_result("Availability Zones", False, f"Failed to check: {e}")

    def _check_cidr_conflicts(self):
        try:
            target_net = ipaddress.ip_network(self.cidr)
        except ValueError:
            self._add_result(
                "VPC CIDR",
                False,
                f"Invalid CIDR: {self.cidr}",
                "Enter a valid CIDR block (e.g., 10.112.0.0/16)",
            )
            return

        try:
            networks = self._gcloud_json(["compute", "networks", "list"])
            if not isinstance(networks, list):
                networks = []

            conflicts = []
            # check subnets directly in the region
            result = subprocess.run(
                [
                    "gcloud",
                    "compute",
                    "networks",
                    "subnets",
                    "list",
                    f"--project={self.project_id}",
                    f"--regions={self.region}",
                    "--format=value(ipCidrRange,network)",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if not line.strip():
                        continue
                    parts = line.split()
                    if parts:
                        try:
                            existing_net = ipaddress.ip_network(parts[0])
                            if target_net.overlaps(existing_net):
                                conflicts.append(parts[0])
                        except ValueError:
                            continue

            if conflicts:
                self._add_result(
                    "VPC CIDR",
                    False,
                    f"{self.cidr} conflicts with existing subnets: {', '.join(conflicts)}",
                    "Choose a non-overlapping CIDR block",
                )
            else:
                self._add_result("VPC CIDR", True, f"{self.cidr} has no conflicts")
        except Exception as e:
            self._add_result("VPC CIDR", False, f"Failed to check: {e}")


class GCPSetupWizard(BaseSetupWizard):
    CONTROL_PLANE_KEYS = ("global-env", "api-url", "auth0-domain", "amp-aws-account-id")
    HEADER_TITLE = "Pinecone BYOC Setup Wizard - GCP"
    HEADER_SUBTITLE = "This wizard will set up everything you need to deploy Pinecone BYOC on GCP."
    DEFAULT_CIDR = "10.112.0.0/16"
    DELETION_PROTECTION_DESC = "Protect AlloyDB databases and GCS buckets from accidental deletion"
    PRIVATE_ACCESS_DESC = "Private access requires Private Service Connect (more secure)"
    METADATA_NAME = "labels"
    CLOUD_NAME = "GCP"

    def run(self, output_dir: str = ".") -> bool:
        if self._headless:
            return self._run_headless(output_dir)

        self._print_header()
        self._maybe_resume(output_dir)

        api_key = self._get_api_key()
        if not api_key:
            return False

        if not self._validate_api_key(api_key):
            return False

        project_id = self._validate_gcp_creds()
        if not project_id:
            return False

        project_id = self._get_project_id(project_id)
        region = self._get_region()
        zones = self._get_zones(project_id, region)
        cidr = self._get_cidr()
        deletion_protection = self._get_deletion_protection()
        public_access = self._get_public_access()
        labels = self._get_custom_metadata()

        if not self._run_preflight_checks(project_id, region, zones, cidr):
            return False

        project_name = self._get_project_name()

        if not self._setup_pulumi_backend():
            return False

        return self._generate_project(
            output_dir,
            project_name,
            api_key,
            project_id,
            region,
            zones,
            cidr,
            deletion_protection,
            public_access,
            labels,
        )

    def _run_headless(self, output_dir: str) -> bool:
        console.print("  [dim]Running in headless mode (reading from environment)[/]")

        api_key = os.environ.get("PINECONE_API_KEY")
        if not api_key:
            console.print("  [red]✗[/] PINECONE_API_KEY environment variable is required")
            return False

        project_id = os.environ.get("GCP_PROJECT")
        if not project_id:
            console.print("  [red]✗[/] GCP_PROJECT environment variable is required")
            return False

        region = os.environ.get("PINECONE_REGION", "us-central1")
        zones_str = os.environ.get("PINECONE_AZS", f"{region}-a,{region}-b")
        zones = [z.strip() for z in zones_str.split(",")]
        cidr = os.environ.get("PINECONE_VPC_CIDR", self.DEFAULT_CIDR)
        deletion_protection = (
            os.environ.get("PINECONE_DELETION_PROTECTION", "true").lower() == "true"
        )
        public_access = os.environ.get("PINECONE_PUBLIC_ACCESS", "true").lower() == "true"
        project_name = os.environ.get("PINECONE_PROJECT_NAME", "pinecone-byoc")

        return self._generate_project(
            output_dir,
            project_name,
            api_key,
            project_id,
            region,
            zones,
            cidr,
            deletion_protection,
            public_access,
            {},
            control_plane=self._control_plane_overrides(),
        )

    def _validate_gcp_creds(self) -> str | None:
        console.print()
        console.print(f"  {self._step('GCP Credentials')}")
        console.print()

        project_id = None
        with Status("  [dim]Validating GCP credentials...[/]", console=console, spinner="dots"):
            try:
                try:
                    from google.auth import default

                    credentials, project_id = default()
                    if credentials and project_id:
                        console.print(
                            f"  [green]✓[/] GCP credentials valid [dim](Project: {project_id})[/]"
                        )
                except ImportError:
                    pass

                if not project_id:
                    result = subprocess.run(
                        ["gcloud", "config", "get-value", "project"],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        project_id = result.stdout.strip()
                        console.print(
                            f"  [green]✓[/] GCP credentials valid [dim](Project: {project_id})[/]"
                        )
                    else:
                        raise Exception("Could not determine GCP project")

            except Exception as e:
                console.print(f"  [red]✗[/] GCP credentials invalid: {e}")
                console.print()
                console.print("  [dim]Make sure you have valid GCP credentials configured.[/]")
                console.print("  [dim]You can set them via:[/]")
                console.print("    [dim]· gcloud auth application-default login[/]")
                console.print("    [dim]· GOOGLE_APPLICATION_CREDENTIALS environment variable[/]")
                console.print("    [dim]· gcloud config set project PROJECT_ID[/]")
                return None

        # check for gke-gcloud-auth-plugin (required for kubectl/Pulumi to auth to GKE)
        try:
            plugin_check = subprocess.run(
                ["gke-gcloud-auth-plugin", "--version"],
                capture_output=True,
                text=True,
            )
            if plugin_check.returncode != 0:
                raise FileNotFoundError
        except FileNotFoundError:
            console.print("  [red]✗[/] gke-gcloud-auth-plugin not found")
            console.print("  [dim]Install it:[/] gcloud components install gke-gcloud-auth-plugin")
            return None
        console.print("  [green]✓[/] gke-gcloud-auth-plugin installed")

        return project_id

    def _get_project_id(self, detected_project: str) -> str:
        console.print()
        console.print(f"  {self._step('GCP Project ID')}")
        console.print()
        return self._prompt("Enter GCP project ID", detected_project, key="project_id")

    def _get_region(self) -> str:
        console.print()
        console.print(f"  {self._step('GCP Region')}")
        console.print()
        return self._prompt("Enter GCP region", "us-central1", key="region")

    def _fetch_zones(self, project_id: str, region: str) -> list[str]:
        try:
            result = subprocess.run(
                [
                    "gcloud",
                    "compute",
                    "zones",
                    "list",
                    "--format=value(name)",
                    f"--project={project_id}",
                    f"--filter=region:{region} AND status:UP",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                zones = sorted([z for z in result.stdout.strip().split("\n") if z])
                if zones:
                    return zones
        except Exception as e:
            console.print(f"  [yellow]⚠[/] Could not fetch zones from GCP: {e}")
        return [f"{region}-a", f"{region}-b", f"{region}-c"]

    def _get_zones(self, project_id: str, region: str) -> list[str]:
        console.print()
        console.print(f"  {self._step('GCP Zones')}")
        console.print()

        with Status("  [dim]Fetching availability zones...[/]", console=console, spinner="dots"):
            available = self._fetch_zones(project_id, region)

        console.print(f"  [dim]Available in {region}:[/] {', '.join(available)}")

        zones_input = self._prompt(
            "Enter zones (comma-separated)", self._zone_default("zones", available), key="zones"
        )
        zones = [zone.strip() for zone in zones_input.split(",")]
        return zones

    def _run_preflight_checks(
        self, project_id: str, region: str, zones: list[str], cidr: str
    ) -> bool:
        console.print()
        console.print(f"  {self._step('Preflight Checks')}")
        console.print()

        checker = GCPPreflightChecker(project_id, region, zones, cidr)
        if not checker.run_checks():
            console.print()
            console.print(
                "  [red]Preflight checks failed. Fix the issues above before proceeding.[/]"
            )
            return False

        return True

    def _generate_project(
        self,
        output_dir: str,
        project_name: str,
        api_key: str,
        project_id: str,
        region: str,
        zones: list[str],
        cidr: str,
        deletion_protection: bool,
        public_access: bool,
        labels: dict[str, str],
        control_plane: dict[str, str] | None = None,
    ):
        console.print()

        if not self._check_pulumi_installed():
            console.print("  [red]✗[/] Pulumi CLI not found")
            console.print("  [dim]Install Pulumi first:[/] https://www.pulumi.com/docs/install/")
            return False

        # create Pulumi.yaml
        pulumi_yaml = {
            "name": project_name,
            "runtime": {
                "name": "python",
                "options": {"virtualenv": ".venv", "toolchain": "uv"},
            },
            "description": "Pinecone BYOC deployment on GCP",
        }

        os.makedirs(output_dir, exist_ok=True)
        pulumi_yaml_path = os.path.join(output_dir, "Pulumi.yaml")
        with open(pulumi_yaml_path, "w") as f:
            yaml.dump(pulumi_yaml, f, default_flow_style=False)
        console.print("  [green]✓[/] Created Pulumi.yaml")

        # create __main__.py
        main_py = '''"""Pinecone BYOC deployment on GCP."""

import pulumi
from pulumi_pinecone_byoc.gcp import PineconeGCPCluster, PineconeGCPClusterArgs

config = pulumi.Config()
gcp_config = pulumi.Config("gcp")

__CONTROL_PLANE__
cluster = PineconeGCPCluster(
    "pinecone-byoc",
    PineconeGCPClusterArgs(
        pinecone_api_key=config.require_secret("pinecone-api-key"),
        pinecone_version=config.require("pinecone-version"),
        project=gcp_config.require("project"),
        region=config.require("region"),
        availability_zones=config.require_object("availability-zones"),
        vpc_cidr=config.get("vpc-cidr") or "10.112.0.0/16",
        deletion_protection=config.get_bool("deletion-protection") if config.get_bool("deletion-protection") is not None else True,
        public_access_enabled=config.get_bool("public-access-enabled") if config.get_bool("public-access-enabled") is not None else True,
        labels=config.get_object("labels") or {},
        **control_plane,
    ),
)

update_kubeconfig_command = cluster.name.apply(
    lambda name: f"gcloud container clusters get-credentials {name} --region {config.require('region')} --project {gcp_config.require('project')}"
)
pulumi.export("environment", cluster.environment.env_name)
pulumi.export("update_kubeconfig_command", update_kubeconfig_command)
if config.get_bool("public-access-enabled") is False:
    pulumi.export("psc_service_attachment", cluster.psc_service_attachment)
'''

        self._write_main_py(output_dir, main_py)

        self._write_pyproject(output_dir, "gcp", self._dev_source)

        # create stack config
        stack_name = self._stack_name
        deletion_protection_str = str(deletion_protection).lower()
        public_access_str = str(public_access).lower()
        config_content = f"""config:
  gcp:project: {project_id}
  {project_name}:region: {region}
  {project_name}:pinecone-version: {PINECONE_VERSION}
  {project_name}:vpc-cidr: {cidr}
  {project_name}:deletion-protection: {deletion_protection_str}
  {project_name}:public-access-enabled: {public_access_str}
  {project_name}:availability-zones:
"""
        for zone in zones:
            config_content += f"    - {zone}\n"

        config_content += self._control_plane_config(project_name, control_plane)

        # add labels if provided (quote values to handle YAML special chars)
        if labels:
            config_content += f"  {project_name}:labels:\n"
            for key, value in labels.items():
                config_content += f'    {key}: "{value}"\n'

        config_path = os.path.join(output_dir, f"Pulumi.{stack_name}.yaml")
        with open(config_path, "w") as f:
            f.write(config_content)
        console.print(f"  [green]✓[/] Created Pulumi.{stack_name}.yaml")

        if self._skip_install:
            return True

        # install dependencies with uv
        with Status("  [dim]Installing dependencies...[/]", console=console, spinner="dots"):
            result = subprocess.run(
                ["uv", "sync"],
                cwd=output_dir,
                capture_output=True,
                text=True,
            )

        if result.returncode == 0:
            # get installed version
            version_result = subprocess.run(
                ["uv", "pip", "show", "pulumi-pinecone-byoc"],
                cwd=output_dir,
                capture_output=True,
                text=True,
            )
            pkg_version = "unknown"
            for line in version_result.stdout.splitlines():
                if line.startswith("Version:"):
                    pkg_version = line.split(":", 1)[1].strip()
                    break
            console.print(
                f"  [green]✓[/] Dependencies installed [dim](pulumi-pinecone-byoc v{pkg_version})[/]"
            )
        else:
            console.print(f"  [red]✗[/] Failed to install dependencies: {result.stderr.strip()}")
            console.print("  [dim]Run manually:[/] uv sync")
            return False

        # init stack
        with Status("  [dim]Initializing stack...[/]", console=console, spinner="dots"):
            result = subprocess.run(
                [
                    "pulumi",
                    "stack",
                    "select",
                    "--create",
                    stack_name,
                    "--cwd",
                    output_dir,
                ],
                capture_output=True,
                text=True,
            )

        if result.returncode == 0:
            console.print(f"  [green]✓[/] Stack {stack_name} ready")
        else:
            console.print(f"  [yellow]⚠[/] Stack init: {result.stderr.strip()}")

        # set api key as secret
        with Status("  [dim]Storing API key securely...[/]", console=console, spinner="dots"):
            result = subprocess.run(
                [
                    "pulumi",
                    "config",
                    "set",
                    "--secret",
                    "pinecone-api-key",
                    api_key,
                    "--stack",
                    stack_name,
                    "--cwd",
                    output_dir,
                ],
                capture_output=True,
                text=True,
            )

        if result.returncode != 0:
            console.print(f"  [red]✗[/] Failed to store API key: {result.stderr.strip()}")
            console.print(
                "  [dim]Run manually:[/] pulumi config set --secret pinecone-api-key <key>"
            )
            return False

        console.print("  [green]✓[/] API key stored securely")

        self._print_success(output_dir)
        return True


# ---------------------------------------------------------------------------
# Azure Setup Wizard
# ---------------------------------------------------------------------------


class AzurePreflightChecker:
    def __init__(self, subscription_id: str, region: str, zones: list[str], cidr: str):
        self.subscription_id = subscription_id
        self.region = region
        self.zones = zones
        self.cidr = cidr
        self.results: list[PreflightResult] = []

    def run_checks(self) -> bool:
        checks = [
            ("Resource Providers", self._check_resource_providers),
            ("PostgreSQL Flexible Server", self._check_postgres_availability),
            ("vCPU Quota", self._check_vcpu_quota),
            ("AKS Clusters", self._check_aks_quota),
            ("VM SKUs", self._check_vm_skus),
            ("Availability Zones", self._check_zones),
            ("VNet CIDR", self._check_cidr_conflicts),
        ]

        for name, check_fn in checks:
            with Status(f"  [dim]Checking {name}...[/]", console=console, spinner="dots"):
                check_fn()

            r = self.results[-1]
            status = "✓" if r.passed else "✗"
            color = "green" if r.passed else "red"
            console.print(f"  [{color}]{status}[/] {r.name}: {r.message}")
            if r.details and not r.passed:
                console.print(f"    [dim]{r.details}[/]")

        failed = [r for r in self.results if not r.passed]
        return len(failed) == 0

    def _add_result(self, name: str, passed: bool, message: str, details: str | None = None):
        result = PreflightResult(name, passed, message, details)
        self.results.append(result)

    def _az_json(self, args: list[str]):
        result = subprocess.run(
            ["az"] + args + ["--output", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip().split("\n")[0])
        return json.loads(result.stdout)

    def _check_resource_providers(self):
        required_providers = [
            "Microsoft.Compute",
            "Microsoft.ContainerService",
            "Microsoft.DBforPostgreSQL",
            "Microsoft.Storage",
            "Microsoft.Network",
            "Microsoft.KeyVault",
            "Microsoft.ManagedIdentity",
            "Microsoft.Authorization",
        ]

        try:
            providers = self._az_json(["provider", "list"])
            registered = {
                p["namespace"] for p in providers if p.get("registrationState") == "Registered"
            }
            missing = [p for p in required_providers if p not in registered]

            if missing:
                self._add_result(
                    "Resource Providers",
                    False,
                    f"{len(missing)} not registered: {', '.join(missing)}",
                    f"Run: az provider register --namespace {missing[0]}",
                )
            else:
                self._add_result(
                    "Resource Providers",
                    True,
                    f"All {len(required_providers)} required providers registered",
                )
        except Exception as e:
            self._add_result("Resource Providers", False, f"Failed to check: {e}")

    def _check_postgres_availability(self):
        try:
            result = subprocess.run(
                [
                    "az",
                    "postgres",
                    "flexible-server",
                    "list-skus",
                    "--location",
                    self.region,
                    "--subscription",
                    self.subscription_id,
                    "--output",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                self._add_result(
                    "PostgreSQL Flexible Server",
                    False,
                    f"Failed: {result.stderr.strip().split(chr(10))[0]}",
                )
                return

            skus = json.loads(result.stdout)
            if not skus:
                self._add_result(
                    "PostgreSQL Flexible Server",
                    False,
                    f"No SKUs available in {self.region}",
                    "Choose a different region",
                )
                return

            # check if provisioning is restricted in this region
            reason = skus[0].get("reason") or ""
            if "restricted" in reason.lower():
                self._add_result(
                    "PostgreSQL Flexible Server",
                    False,
                    f"Provisioning restricted in {self.region}",
                    "Choose a different region or request a quota increase",
                )
                return

            # check that our target SKU (Standard_D2s_v3) is available
            target_sku = "Standard_D2s_v3"
            found = False
            for cap in skus:
                for edition in cap.get("supportedServerEditions", []):
                    if edition.get("name") == "GeneralPurpose":
                        for sku in edition.get("supportedServerSkus", []):
                            if sku.get("name") == target_sku:
                                found = True
                                break

            if found:
                self._add_result(
                    "PostgreSQL Flexible Server",
                    True,
                    f"{target_sku} available in {self.region}",
                )
            else:
                self._add_result(
                    "PostgreSQL Flexible Server",
                    False,
                    f"{target_sku} not available in {self.region}",
                    "Choose a different region or VM SKU",
                )
        except Exception as e:
            self._add_result("PostgreSQL Flexible Server", False, f"Failed to check: {e}")

    def _check_vcpu_quota(self):
        try:
            usages = self._az_json(
                [
                    "vm",
                    "list-usage",
                    "--location",
                    self.region,
                    "--subscription",
                    self.subscription_id,
                ]
            )
            # check total regional vCPUs
            for usage in usages:
                if usage.get("name", {}).get("value") == "cores":
                    current = int(usage.get("currentValue", 0))
                    limit = int(usage.get("limit", 0))
                    available = limit - current
                    # need at least 8 vCPUs for default node pool
                    self._add_result(
                        "vCPU Quota",
                        available >= 8,
                        f"{available} available [dim](using {current}/{limit})[/]",
                        "Request quota increase for 'Total Regional vCPUs'"
                        if available < 8
                        else None,
                    )
                    return
            self._add_result("vCPU Quota", True, "Could not determine quota, skipping")
        except Exception as e:
            self._add_result("vCPU Quota", False, f"Failed to check: {e}")

    def _check_aks_quota(self):
        try:
            clusters = self._az_json(
                [
                    "aks",
                    "list",
                    "--subscription",
                    self.subscription_id,
                ]
            )
            current = len(clusters) if isinstance(clusters, list) else 0
            quota = 100
            available = quota - current
            self._add_result(
                "AKS Clusters",
                available >= 1,
                f"{available} available [dim](using {current}/{quota})[/]",
            )
        except Exception as e:
            self._add_result("AKS Clusters", False, f"Failed to check: {e}")

    def _check_vm_skus(self):
        vm_skus = [
            "Standard_D4s_v5",
            "Standard_L2aos_v4",
            "Standard_L2s_v4",
            "Standard_L4s_v4",
        ]
        try:
            result = subprocess.run(
                [
                    "az",
                    "rest",
                    "--method",
                    "get",
                    "--url",
                    f"https://management.azure.com/subscriptions/{self.subscription_id}"
                    f"/providers/Microsoft.Compute/skus?api-version=2021-07-01"
                    f"&$filter=location eq '{self.region}'",
                    "--output",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                self._add_result("VM SKUs", False, f"Failed: {result.stderr.strip()}")
                return

            data = json.loads(result.stdout)
            available_skus: set[str] = set()
            for sku in data.get("value", []):
                if sku.get("resourceType") != "virtualMachines":
                    continue
                sku_name = sku.get("name", "")
                restrictions = sku.get("restrictions", [])
                is_restricted = any(r.get("type") == "Location" for r in restrictions)
                if not is_restricted:
                    available_skus.add(sku_name)

            unavailable = [s for s in vm_skus if s not in available_skus]
            self._add_result(
                "VM SKUs",
                len(unavailable) == 0,
                "All required SKUs available"
                if not unavailable
                else f"Unavailable: {', '.join(unavailable)}",
                "Choose a different region" if unavailable else None,
            )
        except Exception as e:
            self._add_result("VM SKUs", False, f"Failed to check: {e}")

    def _check_zones(self):
        try:
            # use REST API directly - much faster than `az vm list-skus` CLI
            result = subprocess.run(
                [
                    "az",
                    "rest",
                    "--method",
                    "get",
                    "--url",
                    f"https://management.azure.com/subscriptions/{self.subscription_id}"
                    f"/providers/Microsoft.Compute/skus?api-version=2021-07-01"
                    f"&$filter=location eq '{self.region}'",
                    "--output",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                self._add_result("Availability Zones", False, f"Failed: {result.stderr.strip()}")
                return

            data = json.loads(result.stdout)
            required_skus = [
                "Standard_D4s_v5",
                "Standard_L2aos_v4",
                "Standard_L2s_v4",
                "Standard_L4s_v4",
            ]
            per_sku_zones: dict[str, set[str]] = {}
            for sku in data.get("value", []):
                if (
                    sku.get("resourceType") != "virtualMachines"
                    or sku.get("name") not in required_skus
                ):
                    continue
                sku_name = sku["name"]
                zones_for_sku = per_sku_zones.setdefault(sku_name, set())
                for loc in sku.get("locationInfo", []):
                    zones_for_sku.update(loc.get("zones", []))
                for restriction in sku.get("restrictions", []):
                    if restriction.get("type") == "Zone":
                        rz = restriction.get("restrictionInfo", {}).get("zones", [])
                        zones_for_sku -= set(rz)

            available_zones = None
            for sku_zones in per_sku_zones.values():
                available_zones = (
                    sku_zones if available_zones is None else available_zones & sku_zones
                )

            if not available_zones:
                missing = [s for s in required_skus if s not in per_sku_zones]
                self._add_result(
                    "Availability Zones",
                    False,
                    "No zones available for all required SKUs"
                    + (f" (missing: {', '.join(missing)})" if missing else ""),
                )
                return

            invalid = [z for z in self.zones if z not in available_zones]
            if invalid:
                self._add_result(
                    "Availability Zones",
                    False,
                    f"Zones not available: {', '.join(invalid)}",
                    f"Valid zones for {self.region}: {', '.join(sorted(available_zones))}",
                )
            else:
                self._add_result("Availability Zones", True, "All requested zones available")
        except Exception as e:
            self._add_result("Availability Zones", False, f"Failed to check: {e}")

    def _check_cidr_conflicts(self):
        try:
            aks_net = ipaddress.ip_network(self.cidr)
        except ValueError:
            self._add_result(
                "VNet CIDR",
                False,
                f"Invalid CIDR: {self.cidr}",
                "Enter a valid CIDR block (e.g., 10.0.0.0/16)",
            )
            return

        if aks_net.prefixlen > 20:
            self._add_result(
                "VNet CIDR",
                False,
                f"CIDR /{aks_net.prefixlen} is too small (minimum is /20)",
                "Use a /20 or larger CIDR block (e.g., 10.0.0.0/16) to ensure enough IP addresses for node scaling.",
            )
            return

        # derive subnets the same way vnet.py does
        try:
            db_net = ipaddress.ip_network(
                f"{aks_net.network_address + aks_net.num_addresses}/{aks_net.prefixlen}"
            )
            pls_net = ipaddress.ip_network(f"{db_net.network_address + db_net.num_addresses}/27")
        except ValueError:
            self._add_result(
                "VNet CIDR",
                False,
                f"CIDR {self.cidr} is too small to derive required subnets",
                "Use a /16 or larger CIDR block (e.g., 10.0.0.0/16)",
            )
            return
        aks_service_cidr = ipaddress.ip_network("112.0.0.0/16")

        # check derived subnets don't overlap AKS service CIDR
        all_nets = [("AKS subnet", aks_net), ("DB subnet", db_net), ("PLS subnet", pls_net)]
        service_conflicts = []
        for label, net in all_nets:
            if net.overlaps(aks_service_cidr):
                service_conflicts.append(f"{label} ({net})")
        if service_conflicts:
            self._add_result(
                "VNet CIDR",
                False,
                f"Overlaps AKS service CIDR 112.0.0.0/16: {', '.join(service_conflicts)}",
                "Choose a CIDR that doesn't overlap 112.0.0.0/16",
            )
            return

        try:
            # Enumerate existing VNets subscription-wide via the ARM REST API.
            # `az network vnet list` cannot list across an entire subscription on
            # recent Azure CLI versions (the migrated `aaz` module marks
            # --resource-group as required), so use `az rest`, which is built into
            # the CLI core and supports subscription-wide listing with pagination.
            vnets = []
            url = (
                "https://management.azure.com/subscriptions/"
                f"{self.subscription_id}/providers/Microsoft.Network/"
                "virtualNetworks?api-version=2023-09-01"
            )
            while url:
                resp = self._az_json(["rest", "--method", "get", "--url", url])
                if not isinstance(resp, dict):
                    break
                vnets.extend(resp.get("value", []) or [])
                url = resp.get("nextLink")

            # check all derived subnets against existing VNets
            check_nets = [aks_net, db_net, pls_net]
            conflicts = []
            for vnet in vnets:
                address_space = vnet.get("properties", {}).get("addressSpace", {})
                for prefix in address_space.get("addressPrefixes", []):
                    try:
                        existing_net = ipaddress.ip_network(prefix)
                        for net in check_nets:
                            if net.overlaps(existing_net):
                                conflicts.append(f"{net} overlaps {prefix}")
                    except ValueError:
                        continue

            if conflicts:
                self._add_result(
                    "VNet CIDR",
                    False,
                    f"Conflicts with existing VNets: {', '.join(conflicts)}",
                    "Choose a non-overlapping CIDR block",
                )
            else:
                self._add_result("VNet CIDR", True, f"{self.cidr} has no conflicts")
        except Exception as e:
            self._add_result("VNet CIDR", False, f"Failed to check: {e}")


class AzureSetupWizard(BaseSetupWizard):
    CONTROL_PLANE_KEYS = (
        "global-env",
        "api-url",
        "auth0-domain",
        "gcp-project",
        "amp-aws-account-id",
    )
    HEADER_TITLE = "Pinecone BYOC Setup Wizard - Azure"
    HEADER_SUBTITLE = (
        "This wizard will set up everything you need to deploy Pinecone BYOC on Azure."
    )
    DEFAULT_CIDR = "10.0.0.0/16"
    DELETION_PROTECTION_DESC = (
        "Protect PostgreSQL databases and storage accounts from accidental deletion"
    )
    PRIVATE_ACCESS_DESC = "Private access requires Azure Private Link (more secure)"
    METADATA_NAME = "tags"
    CLOUD_NAME = "Azure"

    def run(self, output_dir: str = ".") -> bool:
        if self._headless:
            return self._run_headless(output_dir)

        self._print_header()
        self._maybe_resume(output_dir)

        api_key = self._get_api_key()
        if not api_key:
            return False

        if not self._validate_api_key(api_key):
            return False

        subscription_id = self._validate_azure_creds()
        if not subscription_id:
            return False

        subscription_id = self._get_subscription_id(subscription_id)
        region = self._get_region()
        zones = self._get_zones(subscription_id, region)
        cidr = self._get_cidr()
        deletion_protection = self._get_deletion_protection()
        public_access = self._get_public_access()
        tags = self._get_custom_metadata()

        if not self._run_preflight_checks(subscription_id, region, zones, cidr):
            return False

        project_name = self._get_project_name()

        if not self._setup_pulumi_backend():
            return False

        return self._generate_project(
            output_dir,
            project_name,
            api_key,
            subscription_id,
            region,
            zones,
            cidr,
            deletion_protection,
            public_access,
            tags,
        )

    def _run_headless(self, output_dir: str) -> bool:
        console.print("  [dim]Running in headless mode (reading from environment)[/]")

        api_key = os.environ.get("PINECONE_API_KEY")
        if not api_key:
            console.print("  [red]✗[/] PINECONE_API_KEY environment variable is required")
            return False

        subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
        if not subscription_id:
            console.print("  [red]✗[/] AZURE_SUBSCRIPTION_ID environment variable is required")
            return False

        region = os.environ.get("PINECONE_REGION", "eastus")
        zones_str = os.environ.get("PINECONE_AZS", "1,2")
        zones = [z.strip() for z in zones_str.split(",")]
        cidr = os.environ.get("PINECONE_VPC_CIDR", self.DEFAULT_CIDR)
        deletion_protection = (
            os.environ.get("PINECONE_DELETION_PROTECTION", "true").lower() == "true"
        )
        public_access = os.environ.get("PINECONE_PUBLIC_ACCESS", "true").lower() == "true"
        project_name = os.environ.get("PINECONE_PROJECT_NAME", "pinecone-byoc")

        return self._generate_project(
            output_dir,
            project_name,
            api_key,
            subscription_id,
            region,
            zones,
            cidr,
            deletion_protection,
            public_access,
            {},
            control_plane=self._control_plane_overrides(),
        )

    def _validate_azure_creds(self) -> str | None:
        console.print()
        console.print(f"  {self._step('Azure Credentials')}")
        console.print()

        with Status("  [dim]Validating Azure credentials...[/]", console=console, spinner="dots"):
            try:
                result = subprocess.run(
                    ["az", "account", "show", "--output", "json"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0 and result.stdout.strip():
                    account = json.loads(result.stdout)
                    subscription_id = account.get("id", "")
                    subscription_name = account.get("name", "")
                    console.print(
                        f"  [green]✓[/] Azure credentials valid "
                        f"[dim](Subscription: {subscription_name} / {subscription_id})[/]"
                    )
                    return subscription_id
                else:
                    raise Exception("Could not determine Azure subscription")

            except Exception as e:
                console.print(f"  [red]✗[/] Azure credentials invalid: {e}")
                console.print()
                console.print("  [dim]Make sure you have valid Azure credentials configured.[/]")
                console.print("  [dim]You can set them via:[/]")
                console.print("    [dim]· az login[/]")
                console.print("    [dim]· az account set --subscription SUBSCRIPTION_ID[/]")
                console.print("    [dim]· AZURE_SUBSCRIPTION_ID environment variable[/]")
                return None

    def _get_subscription_id(self, detected_subscription: str) -> str:
        console.print()
        console.print(f"  {self._step('Azure Subscription ID')}")
        console.print()
        return self._prompt(
            "Enter Azure subscription ID", detected_subscription, key="subscription_id"
        )

    def _get_region(self) -> str:
        console.print()
        console.print(f"  {self._step('Azure Region')}")
        console.print()
        return self._prompt("Enter Azure region", "eastus", key="region")

    def _fetch_zones(self, subscription_id: str, region: str) -> list[str]:
        try:
            result = subprocess.run(
                [
                    "az",
                    "rest",
                    "--method",
                    "get",
                    "--url",
                    f"https://management.azure.com/subscriptions/{subscription_id}"
                    f"/providers/Microsoft.Compute/skus?api-version=2021-07-01"
                    f"&$filter=location eq '{region}'",
                    "--output",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                required_skus = [
                    "Standard_D4s_v5",
                    "Standard_L2aos_v4",
                    "Standard_L2s_v4",
                    "Standard_L4s_v4",
                ]
                per_sku_zones: dict[str, set[str]] = {}
                for sku in data.get("value", []):
                    if (
                        sku.get("resourceType") != "virtualMachines"
                        or sku.get("name") not in required_skus
                    ):
                        continue
                    sku_name = sku["name"]
                    zones_for_sku = per_sku_zones.setdefault(sku_name, set())
                    for loc in sku.get("locationInfo", []):
                        zones_for_sku.update(loc.get("zones", []))
                    for restriction in sku.get("restrictions", []):
                        if restriction.get("type") == "Zone":
                            rz = restriction.get("restrictionInfo", {}).get("zones", [])
                            zones_for_sku -= set(rz)
                # intersect: only zones where ALL required SKUs are available
                available = None
                for sku_zones in per_sku_zones.values():
                    available = sku_zones if available is None else available & sku_zones
                if available:
                    return sorted(available)
        except Exception as e:
            console.print(f"  [yellow]⚠[/] Could not fetch zones from Azure: {e}")
        return ["1", "2", "3"]

    def _get_zones(self, subscription_id: str, region: str) -> list[str]:
        console.print()
        console.print(f"  {self._step('Availability Zones')}")
        console.print()

        with Status("  [dim]Fetching availability zones...[/]", console=console, spinner="dots"):
            available = self._fetch_zones(subscription_id, region)

        console.print(f"  [dim]Available in {region}:[/] {', '.join(available)}")

        zones_input = self._prompt(
            "Enter zones (comma-separated)", self._zone_default("zones", available), key="zones"
        )
        zones = [zone.strip() for zone in zones_input.split(",")]
        return zones

    def _run_preflight_checks(
        self, subscription_id: str, region: str, zones: list[str], cidr: str
    ) -> bool:
        console.print()
        console.print(f"  {self._step('Preflight Checks')}")
        console.print()

        checker = AzurePreflightChecker(subscription_id, region, zones, cidr)
        if not checker.run_checks():
            console.print()
            console.print(
                "  [red]Preflight checks failed. Fix the issues above before proceeding.[/]"
            )
            return False

        return True

    def _generate_project(
        self,
        output_dir: str,
        project_name: str,
        api_key: str,
        subscription_id: str,
        region: str,
        zones: list[str],
        cidr: str,
        deletion_protection: bool,
        public_access: bool,
        tags: dict[str, str],
        control_plane: dict[str, str] | None = None,
    ):
        console.print()

        if not self._check_pulumi_installed():
            console.print("  [red]✗[/] Pulumi CLI not found")
            console.print("  [dim]Install Pulumi first:[/] https://www.pulumi.com/docs/install/")
            return False

        pulumi_yaml = {
            "name": project_name,
            "runtime": {
                "name": "python",
                "options": {"virtualenv": ".venv", "toolchain": "uv"},
            },
            "description": "Pinecone BYOC deployment on Azure",
        }

        os.makedirs(output_dir, exist_ok=True)
        pulumi_yaml_path = os.path.join(output_dir, "Pulumi.yaml")
        with open(pulumi_yaml_path, "w") as f:
            yaml.dump(pulumi_yaml, f, default_flow_style=False)
        console.print("  [green]✓[/] Created Pulumi.yaml")

        main_py = '''"""Pinecone BYOC deployment on Azure."""

import pulumi
from pulumi_pinecone_byoc.azure import PineconeAzureCluster, PineconeAzureClusterArgs

config = pulumi.Config()

__CONTROL_PLANE__
cluster = PineconeAzureCluster(
    "pinecone-byoc",
    PineconeAzureClusterArgs(
        pinecone_api_key=config.require_secret("pinecone-api-key"),
        pinecone_version=config.require("pinecone-version"),
        subscription_id=config.require("subscription-id"),
        region=config.require("region"),
        availability_zones=config.require_object("availability-zones"),
        vpc_cidr=config.get("vpc-cidr") or "10.0.0.0/16",
        deletion_protection=config.get_bool("deletion-protection") if config.get_bool("deletion-protection") is not None else True,
        public_access_enabled=config.get_bool("public-access-enabled") if config.get_bool("public-access-enabled") is not None else True,
        tags=config.get_object("tags"),
        **control_plane,
    ),
)

region = config.require("region")
update_kubeconfig_command = cluster.name.apply(
    lambda name: f"az aks get-credentials --resource-group {name.removeprefix('cluster-')}-{region}-rg --name {name}"
)
pulumi.export("environment", cluster.environment.env_name)
pulumi.export("update_kubeconfig_command", update_kubeconfig_command)
if config.get_bool("public-access-enabled") is False:
    pulumi.export("private_link_service_name", cluster.private_link_service_name)
    pulumi.export("private_link_service_resource_group", cluster.private_link_service_resource_group)
'''

        self._write_main_py(output_dir, main_py)

        self._write_pyproject(output_dir, "azure", self._dev_source)

        stack_name = self._stack_name
        deletion_protection_str = str(deletion_protection).lower()
        public_access_str = str(public_access).lower()
        config_content = f"""config:
  {project_name}:subscription-id: {subscription_id}
  {project_name}:region: {region}
  {project_name}:pinecone-version: {PINECONE_VERSION}
  {project_name}:vpc-cidr: {cidr}
  {project_name}:deletion-protection: {deletion_protection_str}
  {project_name}:public-access-enabled: {public_access_str}
  {project_name}:availability-zones:
"""
        for zone in zones:
            config_content += f'    - "{zone}"\n'

        config_content += self._control_plane_config(project_name, control_plane)

        if tags:
            config_content += f"  {project_name}:tags:\n"
            for key, value in tags.items():
                config_content += f'    {key}: "{value}"\n'

        config_path = os.path.join(output_dir, f"Pulumi.{stack_name}.yaml")
        with open(config_path, "w") as f:
            f.write(config_content)
        console.print(f"  [green]✓[/] Created Pulumi.{stack_name}.yaml")

        if self._skip_install:
            return True

        with Status("  [dim]Installing dependencies...[/]", console=console, spinner="dots"):
            result = subprocess.run(
                ["uv", "sync"],
                cwd=output_dir,
                capture_output=True,
                text=True,
            )

        if result.returncode == 0:
            version_result = subprocess.run(
                ["uv", "pip", "show", "pulumi-pinecone-byoc"],
                cwd=output_dir,
                capture_output=True,
                text=True,
            )
            pkg_version = "unknown"
            for line in version_result.stdout.splitlines():
                if line.startswith("Version:"):
                    pkg_version = line.split(":", 1)[1].strip()
                    break
            console.print(
                f"  [green]✓[/] Dependencies installed "
                f"[dim](pulumi-pinecone-byoc v{pkg_version})[/]"
            )
        else:
            console.print(f"  [red]✗[/] Failed to install dependencies: {result.stderr.strip()}")
            console.print("  [dim]Run manually:[/] uv sync")
            return False

        with Status("  [dim]Initializing stack...[/]", console=console, spinner="dots"):
            result = subprocess.run(
                [
                    "pulumi",
                    "stack",
                    "select",
                    "--create",
                    stack_name,
                    "--cwd",
                    output_dir,
                ],
                capture_output=True,
                text=True,
            )

        if result.returncode == 0:
            console.print(f"  [green]✓[/] Stack {stack_name} ready")
        else:
            console.print(f"  [yellow]⚠[/] Stack init: {result.stderr.strip()}")

        with Status("  [dim]Storing API key securely...[/]", console=console, spinner="dots"):
            result = subprocess.run(
                [
                    "pulumi",
                    "config",
                    "set",
                    "--secret",
                    "pinecone-api-key",
                    api_key,
                    "--stack",
                    stack_name,
                    "--cwd",
                    output_dir,
                ],
                capture_output=True,
                text=True,
            )

        if result.returncode != 0:
            console.print(f"  [red]✗[/] Failed to store API key: {result.stderr.strip()}")
            console.print(
                "  [dim]Run manually:[/] pulumi config set --secret pinecone-api-key <key>"
            )
            return False

        console.print("  [green]✓[/] API key stored securely")

        self._print_success(output_dir)
        return True


def select_cloud() -> str:
    console.print()
    console.print(
        Panel.fit(
            f"[bold {BLUE}]Pinecone BYOC Setup Wizard[/]",
            border_style=BLUE,
            padding=(0, 2),
        )
    )
    console.print()
    console.print(
        "  This wizard will set up everything you need to deploy Pinecone BYOC.",
        style="dim",
    )
    console.print()

    console.print(f"  [bold {BLUE}]Select Cloud Provider[/]")
    console.print()
    console.print("  [1] AWS")
    console.print("  [2] GCP")
    console.print("  [3] Azure")
    console.print()

    cloud = read_input_with_placeholder("Enter choice (1, 2, or 3)", "1")

    if cloud == "1":
        return "aws"
    elif cloud == "2":
        return "gcp"
    elif cloud == "3":
        return "azure"
    else:
        console.print(f"  [red]✗[/] Invalid choice: {cloud}")
        console.print("  [dim]Please choose 1 (AWS), 2 (GCP), or 3 (Azure)[/]")
        sys.exit(1)


UPGRADE_ENV = {
    "region": "PINECONE_REGION",
    "vpc-cidr": "PINECONE_VPC_CIDR",
    "existing-vpc-id": "PINECONE_EXISTING_VPC_ID",
    "public-access-enabled": "PINECONE_PUBLIC_ACCESS",
    "deletion-protection": "PINECONE_DELETION_PROTECTION",
    "custom-ami-id": "PINECONE_CUSTOM_AMI_ID",
    "kms-key-arn": "PINECONE_KMS_KEY_ARN",
    "global-env": "PINECONE_GLOBAL_ENV",
    "api-url": "PINECONE_API_URL",
    "auth0-domain": "PINECONE_AUTH0_DOMAIN",
    "gcp-project": "PINECONE_GCP_PROJECT",
    "subscription-id": "AZURE_SUBSCRIPTION_ID",
    "project-id": "GOOGLE_PROJECT",
}


def _read_yaml(path: str) -> dict:
    with open(path) as handle:
        return yaml.safe_load(handle) or {}


def _find_stack(output_dir: str, stack_name: str | None) -> str | None:
    found = sorted(
        name[len("Pulumi.") : -len(".yaml")]
        for name in os.listdir(output_dir)
        if name.startswith("Pulumi.") and name.endswith(".yaml") and name != "Pulumi.yaml"
    )
    if stack_name:
        if stack_name not in found:
            console.print(f"  [red]✗[/] {output_dir} has no Pulumi.{stack_name}.yaml")
            console.print(f"  [dim]Stacks found:[/] {', '.join(found) or 'none'}")
            return None
        return stack_name
    if len(found) == 1:
        return found[0]
    if not found:
        console.print(f"  [red]✗[/] {output_dir} holds no stack configuration to upgrade")
        return None
    console.print(f"  [dim]Stacks in this project:[/] {', '.join(found)}")
    return read_input_with_cycle("  Which stack are you upgrading?", found) or None


def _upgrade_environment(project: str, config: dict) -> dict[str, str]:
    env = {}
    for key, value in config.items():
        _, _, bare = key.partition(":")
        if key.startswith("aws:") or bare not in UPGRADE_ENV or isinstance(value, dict):
            continue
        env[UPGRADE_ENV[bare]] = str(value).lower() if isinstance(value, bool) else str(value)
    zones = config.get(f"{project}:availability-zones") or config.get(f"{project}:zones")
    if isinstance(zones, list):
        env["PINECONE_AZS"] = ",".join(str(zone) for zone in zones)
    tables = config.get(f"{project}:existing-route-table-ids")
    if isinstance(tables, dict):
        env["PINECONE_ROUTE_TABLE_IDS"] = ",".join(f"{az}={rtb}" for az, rtb in tables.items())
    return env


def upgrade_project(output_dir: str, stack_name: str | None, dev_source: str | None) -> bool:
    console.print()
    console.print(f"  [{BLUE}]Upgrade[/] · {output_dir}")
    console.print()

    project_file = os.path.join(output_dir, "Pulumi.yaml")
    program = os.path.join(output_dir, "__main__.py")
    if not os.path.isfile(project_file) or not os.path.isfile(program):
        console.print(f"  [red]✗[/] {output_dir} is not a generated project")
        console.print("  [dim]Run without --upgrade to create one.[/]")
        return False

    project = str(_read_yaml(project_file).get("name") or "")
    stack = _find_stack(output_dir, stack_name)
    if not project or not stack:
        return False

    cloud = next(
        (c for c in ("aws", "gcp", "azure") if f"pulumi_pinecone_byoc.{c}" in open(program).read()),
        "",
    )
    if not cloud:
        console.print(f"  [red]✗[/] cannot tell which cloud {program} deploys")
        return False

    stack_file = os.path.join(output_dir, f"Pulumi.{stack}.yaml")
    pyproject = os.path.join(output_dir, "pyproject.toml")
    rev_before = pinned_rev(pyproject)
    before = _read_yaml(stack_file).get("config") or {}
    console.print(f"  [green]✓[/] {project}/{stack} on {cloud}, {len(before)} config value(s)")

    env = _upgrade_environment(project, before)
    env["PINECONE_PROJECT_NAME"] = project
    # the key stays encrypted in the stack file; the generator only needs a non-empty value
    env.setdefault("PINECONE_API_KEY", "kept-from-the-existing-stack")
    tags = before.get(f"{project}:tags")

    previous = dict(os.environ)
    os.environ.update(env)
    try:
        generated = run_setup(
            output_dir=output_dir,
            cloud=cloud,
            headless=True,
            stack_name=stack,
            skip_install=True,
            dev_source=dev_source,
        )
    finally:
        os.environ.clear()
        os.environ.update(previous)
    if not generated:
        return False

    after = _read_yaml(stack_file)
    config = after.get("config") or {}
    carried = []
    for key, value in before.items():
        if key not in config:
            config[key] = value
            carried.append(key)
    changed = [k for k, v in config.items() if k in before and before[k] != v]
    if tags is not None:
        config[f"{project}:tags"] = tags
    after["config"] = config
    with open(stack_file, "w") as handle:
        yaml.dump(after, handle, default_flow_style=False, sort_keys=True)

    console.print(
        f"  [green]✓[/] Carried over {len(carried)} value(s) the generator does not write"
    )
    for key in carried:
        console.print(f"      [dim]{key}[/]")
    for key in changed:
        console.print(f"  [yellow]→[/] {key}: {before[key]} becomes {config[key]}")

    rev_after = pinned_rev(pyproject)
    if rev_after and rev_after != rev_before:
        console.print(
            f"  [yellow]→[/] module: {(rev_before or 'unpinned')[:12]} becomes {rev_after[:12]}"
        )
    elif rev_after:
        console.print(f"  [green]✓[/] Module already at {rev_after[:12]}")

    console.print()
    console.print("  [bold]Apply it when you are ready:[/]")
    console.print(f"    uv sync --directory {output_dir}")
    console.print(f"    pulumi -C {output_dir} preview")
    console.print(f"    pulumi -C {output_dir} up")
    console.print()
    return True


def run_setup(
    output_dir: str = ".",
    cloud: str | None = None,
    headless: bool = False,
    stack_name: str = "prod",
    skip_install: bool = False,
    dev_source: str | None = None,
) -> bool:
    try:
        if not cloud:
            if headless:
                console.print("  [red]✗[/] --cloud is required in headless mode")
                return False
            cloud = select_cloud()

        match cloud:
            case "aws":
                wizard_cls = AWSSetupWizard
            case "gcp":
                wizard_cls = GCPSetupWizard
            case "azure":
                wizard_cls = AzureSetupWizard
            case _:
                console.print(f"  [red]✗[/] Unknown cloud provider: {cloud}")
                console.print("  [dim]Valid options: aws, gcp, azure[/]")
                return False

        wizard = wizard_cls(
            headless=headless,
            stack_name=stack_name,
            skip_install=skip_install,
            dev_source=dev_source,
        )
        return wizard.run(output_dir)

    except KeyboardInterrupt:
        console.print()
        console.print("  [yellow]Setup cancelled by user[/]")
        return False
    except Exception as e:
        console.print()
        console.print(f"  [red]✗[/] Setup failed: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pinecone BYOC Setup Wizard")
    parser.add_argument("--output-dir", default=".", help="Directory to write project files")
    parser.add_argument(
        "--cloud",
        choices=["aws", "gcp", "azure"],
        help="Cloud provider (aws, gcp, or azure). If not specified, you will be prompted.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without interactive prompts. Reads all inputs from environment variables.",
    )
    parser.add_argument(
        "--stack-name",
        default="prod",
        help="Pulumi stack name (default: prod).",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip dependency installation and stack initialization.",
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Rewrite an existing project in --output-dir for this version of the module: "
        "reads everything from the stack it already has, keeps its credentials, and prints "
        "the commands to apply the change instead of deploying.",
    )
    parser.add_argument(
        "--dev",
        nargs="?",
        const="",
        metavar="PATH",
        help="Dev mode: point the generated project at a local pulumi-pinecone-byoc "
        "checkout instead of PyPI. Defaults to the checkout this script lives in; pass "
        "a path when running a copy of the script from outside it (as bootstrap.sh does).",
    )
    args = parser.parse_args()

    dev_source = args.dev
    if dev_source == "":
        dev_source = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if dev_source and not os.path.isdir(os.path.join(dev_source, "pulumi_pinecone_byoc")):
        console.print(f"  [red]✗[/] --dev: {dev_source} is not a pulumi-pinecone-byoc checkout")
        console.print("  [dim]Pass the checkout path explicitly: --dev /path/to/repo[/]")
        sys.exit(1)

    if args.upgrade:
        upgraded = upgrade_project(
            args.output_dir,
            args.stack_name if "--stack-name" in sys.argv else None,
            dev_source,
        )
        sys.exit(0 if upgraded else 1)

    success = run_setup(
        args.output_dir,
        args.cloud,
        headless=args.headless,
        stack_name=args.stack_name,
        skip_install=args.skip_install,
        dev_source=dev_source,
    )
    sys.exit(0 if success else 1)
