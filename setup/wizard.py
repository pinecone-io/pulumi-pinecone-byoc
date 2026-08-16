"""Pinecone BYOC setup wizard."""

import argparse
import contextlib
import ipaddress
import json
import os
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

MIN_VPC_PREFIX = 16
MAX_VPC_PREFIX = 20
MAX_AZS = 3

# the layout, mirrored from pulumi_pinecone_byoc.aws.vpc_subnet: bootstrap.sh fetches
# this file and autocomplete.py into a directory with no package in it, so the module
# cannot be imported here. tests/wizard/test_existing_vpc.py holds the two to the same
# answer
PUBLIC_PREFIX_ON_A_SLICE = 26
SLOT_BITS = 4
PRIVATE_SLOTS = 4
PRIVATE_FIRST_SLOT = 4

RFC1918_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]

# every subnet the module creates carries it, so one that has it is ours from an
# earlier run rather than theirs to keep clear of
MANAGED_BY = ("pinecone:managed-by", "pulumi")


def subnet_cidr(vpc_cidr, index: int, is_public: bool):
    network = ipaddress.ip_network(vpc_cidr)
    slots = list(network.subnets(prefixlen_diff=SLOT_BITS))

    if is_public:
        slot = slots[index]
        prefix = (
            network.prefixlen + SLOT_BITS
            if network.prefixlen == MIN_VPC_PREFIX
            else max(PUBLIC_PREFIX_ON_A_SLICE, slot.prefixlen)
        )
    else:
        slot = slots[PRIVATE_FIRST_SLOT + index * PRIVATE_SLOTS]
        prefix = network.prefixlen + PRIVATE_SLOTS // 2

    return ipaddress.ip_network((slot.network_address, prefix))


def _is_ours(subnet) -> bool:
    key, value = MANAGED_BY
    return any(tag["Key"] == key and tag["Value"] == value for tag in subnet.get("Tags", []))


DEFAULT_CIDR_SENTINEL = "default"

console = Console()


@dataclass
class PreflightResult:
    name: str
    passed: bool
    message: str
    details: str | None = None


class NonInteractiveInputRequired(Exception):
    def __init__(self, message: str, env_var: str | None = None):
        super().__init__(message)
        self.field = message
        self.env_var = env_var


# ---------------------------------------------------------------------------
# Resumable answer state
# ---------------------------------------------------------------------------


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
        non_interactive: bool = False,
        stack_name: str = "prod",
        skip_install: bool = False,
        dev_source: str | None = None,
        destroy: bool = False,
    ):
        self.results: list[PreflightResult] = []
        self._current_step = 0
        self._non_interactive = non_interactive
        self._destroy = destroy
        self._stack_name = stack_name
        self._skip_install = skip_install
        # resumable answer state (created by _maybe_resume; None in non-interactive)
        self._state: WizardState | None = None
        self._dev_source = dev_source

    @staticmethod
    def _in_columns(values: list[str], per_line: int = 5) -> list[str]:
        width = max((len(value) for value in values), default=0)
        return [
            "  ".join(value.ljust(width) for value in values[start : start + per_line]).rstrip()
            for start in range(0, len(values), per_line)
        ]

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

        if self._non_interactive:
            value = os.environ.get(key, "") if key else ""
            if value:
                return value
            if default is not None:
                return default
            raise NonInteractiveInputRequired(message, key)

        if options:
            value = read_input_with_cycle(message, options, effective_default)
        else:
            value = read_input_with_placeholder(message, effective_default, password)

        if key and self._state is not None and not password:
            self._state.set(key, value)
        return value

    @staticmethod
    def _yes(response: str) -> bool:
        return response.strip().lower() in ("", "y", "yes", "true", "1")

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

        saved_region = self._state.get("PINECONE_REGION")
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

    def _validated_api_key(self, output_dir: str) -> str | None:
        if not self._non_interactive:
            self._print_header()
            self._maybe_resume(output_dir)

        api_key = self._get_api_key()
        if not api_key:
            return None

        if not self._destroy and not self._validate_api_key(api_key):
            return None

        return api_key

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

        base = self._control_plane_overrides().get("api-url", "https://api.pinecone.io")

        with Status("  [dim]Checking API key...[/]", console=console, spinner="dots"):
            try:
                req = urllib.request.Request(
                    f"{base.rstrip('/')}/indexes",
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

    def _non_interactive_cidr(self) -> str | None:
        cidr = os.environ.get("PINECONE_VPC_CIDR")
        if not cidr:
            return None
        return self.DEFAULT_CIDR if cidr == DEFAULT_CIDR_SENTINEL else cidr

    def _get_cidr(self) -> str:
        console.print()
        console.print(f"  {self._step('VPC CIDR Block')}")
        console.print(f"  [dim]{self.CIDR_DESC}[/]")
        console.print()
        if self._non_interactive:
            return self._non_interactive_cidr() or self.DEFAULT_CIDR
        return self._prompt("Enter CIDR block", self.DEFAULT_CIDR, key="PINECONE_VPC_CIDR")

    def _get_deletion_protection(self) -> bool:
        console.print()
        console.print(f"  {self._step('Deletion Protection')}")
        console.print(f"  [dim]{self.DELETION_PROTECTION_DESC}[/]")
        console.print()
        response = self._prompt(
            "Enable deletion protection? (Y/n)",
            "Y",
            key="PINECONE_DELETION_PROTECTION",
        )
        return self._yes(response)

    def _get_public_access(self, default: str = "Y", note: str = "") -> bool:
        console.print()
        console.print(f"  {self._step('Network Access')}")
        console.print("  [dim]Public access allows connections from the internet[/]")
        console.print(f"  [dim]{self.PRIVATE_ACCESS_DESC}[/]")
        if note:
            console.print(f"  [dim]{note}[/]")
        console.print()
        label = "Y/n" if self._yes(default) else "y/N"
        response = self._prompt(
            f"Enable public access? ({label})", default, key="PINECONE_PUBLIC_ACCESS"
        )
        return self._yes(response)

    def _get_custom_metadata(self) -> dict[str, str]:
        name = self.METADATA_NAME
        console.print()
        console.print(f"  {self._step(f'Resource {name.title()}')}")
        console.print(
            f"  [dim]Add custom {name} to all {self.CLOUD_NAME} resources (for cost tracking, etc.)[/]"
        )
        console.print("  [dim]Format: key=value, comma-separated (e.g., team=platform,env=prod)[/]")
        console.print()

        input_val = self._prompt(
            f"Enter {name} (or press Enter to skip)",
            "",
            key=f"PINECONE_{name.upper()}",
        )
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
        return self._prompt("Enter project name", "pinecone-byoc", key="PINECONE_PROJECT_NAME")

    def _setup_pulumi_backend(self) -> bool:
        console.print()
        console.print(f"  {self._step('Pulumi Backend')}")
        console.print("  [dim]Where to store infrastructure state[/]")
        console.print()

        backend = self._prompt("Backend (local/cloud)", "local", key="PULUMI_BACKEND").lower()
        use_local = backend != "cloud"

        if use_local:
            console.print()
            console.print("  [dim]Enter a passphrase to encrypt secrets (remember this!)[/]")
            passphrase = self._prompt("Passphrase", password=True, key="PULUMI_CONFIG_PASSPHRASE")
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
    def __init__(
        self,
        region: str,
        azs: list[str],
        cidr: str,
        vpc_id: str | None = None,
        route_table_ids: dict[str, str] | None = None,
        public_access: bool = True,
        tags: dict[str, str] | None = None,
        non_interactive: bool = False,
    ):
        import boto3

        self.non_interactive = non_interactive

        self.region = region
        self.azs = azs
        self.cidr = cidr
        self.vpc_id = vpc_id
        self.route_table_ids = route_table_ids
        self.public_access = public_access
        self.tags = tags or {}
        self.results: list[PreflightResult] = []

        self.ec2 = boto3.client("ec2", region_name=region)
        self.eks = boto3.client("eks", region_name=region)
        self.servicequotas = boto3.client("service-quotas", region_name=region)

    def run_checks(self) -> bool:
        # deploying into their VPC asks nothing of our own VPC, gateway or EIP quota,
        # and the range has to fit beside what they already carry rather than beside
        # our other VPCs
        if self.vpc_id:
            checks = [
                ("VPC Exists", self._check_vpc_exists),
                ("VPC DNS", self._check_vpc_dns),
                ("VPC CIDR", self._check_range_fits_their_vpc),
                ("VPC Permissions", self._check_permissions),
                ("Subnet Egress", self._check_their_egress),
                ("EKS Clusters", self._check_eks_cluster_quota),
                *([("Internet Gateway", self._check_igw_attached)] if self.public_access else []),
                ("Network Load Balancers", self._check_nlb_quota),
                ("Availability Zones", self._check_az_availability),
                ("Instance Types", self._check_instance_types),
            ]
        else:
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

    def _check_vpc_exists(self):
        try:
            vpcs = self.ec2.describe_vpcs(VpcIds=[self.vpc_id]).get("Vpcs", [])
        except Exception as e:  # noqa: BLE001 - reported as a failed check
            self._add_result("VPC Exists", False, f"Could not read {self.vpc_id}", str(e))
            return
        if not vpcs:
            self._add_result("VPC Exists", False, f"No VPC {self.vpc_id} in {self.region}")
            return
        self._add_result("VPC Exists", True, f"{self.vpc_id} in {self.region}")

    def _check_vpc_dns(self):
        try:
            support = self.ec2.describe_vpc_attribute(
                VpcId=self.vpc_id, Attribute="enableDnsSupport"
            )["EnableDnsSupport"]["Value"]
            hostnames = self.ec2.describe_vpc_attribute(
                VpcId=self.vpc_id, Attribute="enableDnsHostnames"
            )["EnableDnsHostnames"]["Value"]
        except Exception as e:  # noqa: BLE001 - reported as a failed check
            self._add_result("VPC DNS", False, "Failed to check", str(e))
            return

        if support and hostnames:
            self._add_result("VPC DNS", True, "DNS support and hostnames are on")
            return
        self._add_result(
            "VPC DNS",
            False,
            f"{self.vpc_id} has DNS support={support}, hostnames={hostnames}",
            "EKS needs both; enable them on the VPC before deploying",
        )

    def _check_range_fits_their_vpc(self):
        ours = self._laid_out_range()
        if ours is None:
            return

        try:
            vpcs = self.ec2.describe_vpcs(VpcIds=[self.vpc_id]).get("Vpcs", [])
            theirs = [
                association["CidrBlock"]
                for association in vpcs[0].get("CidrBlockAssociationSet", [])
                if association.get("CidrBlockState", {}).get("State") == "associated"
            ]
            subnets = self.ec2.describe_subnets(
                Filters=[{"Name": "vpc-id", "Values": [self.vpc_id]}]
            )["Subnets"]
        except Exception as e:  # noqa: BLE001 - reported as a failed check
            self._add_result("VPC CIDR", False, "Failed to check", str(e))
            return

        # a range they carry can still be one they use, and the layout falls at fixed
        # offsets in it rather than around whatever is in the way. only the offsets
        # this deploy cuts are asked about: nothing public is cut without public access,
        # and a slot is spare either way
        laid_out = [
            subnet_cidr(ours, index, is_public)
            for index in range(len(self.azs))
            for is_public in ((True, False) if self.public_access else (False,))
        ]
        taken = [
            f"{net} overlaps their {subnet['CidrBlock']} ({subnet['SubnetId']})"
            for subnet in subnets
            if not _is_ours(subnet)
            for net in laid_out
            if net.overlaps(ipaddress.ip_network(subnet["CidrBlock"]))
        ]
        if taken:
            self._add_result(
                "VPC CIDR",
                False,
                "; ".join(taken),
                "Their subnets are cut from those same addresses; pick a range whose "
                "layout none of them is in",
            )
            return

        covered = [c for c in theirs if ours.subnet_of(ipaddress.ip_network(c))]
        if covered:
            self._add_result("VPC CIDR", True, f"{ours} is free inside their {covered[0]}")
            return

        family = [
            block
            for block in RFC1918_RANGES
            if any(ipaddress.ip_network(c).subnet_of(block) for c in theirs)
        ]
        if family and not any(ours.subnet_of(block) for block in family):
            self._add_result(
                "VPC CIDR",
                False,
                f"{ours} is not in {', '.join(str(b) for b in family)}, which {self.vpc_id} uses",
                "AWS refuses a secondary CIDR outside the RFC 1918 range the VPC "
                "already numbers from; pick one inside it",
            )
            return

        clashing = [c for c in theirs if ours.overlaps(ipaddress.ip_network(c))]
        if clashing:
            self._add_result(
                "VPC CIDR",
                False,
                f"{ours} overlaps {', '.join(clashing)} without being inside it",
                "Pick a range the VPC does not already carry, or one wholly inside one",
            )
            return
        self._add_result("VPC CIDR", True, f"{ours} is free to associate with {self.vpc_id}")

    @staticmethod
    def _egress_target(route):
        """What a default route leaves by, or nothing when it cannot leave.

        A peering connection routes nothing transitively, so the peer's NAT and
        gateway are unreachable; an internet gateway is how their public subnets get
        out, not their private ones.
        """
        if route.get("DestinationCidrBlock") != "0.0.0.0/0" or route.get("State") != "active":
            return None
        target = (
            route.get("NatGatewayId")
            or route.get("TransitGatewayId")
            or route.get("VpcEndpointId")
            or route.get("NetworkInterfaceId")
            or route.get("InstanceId")
            or route.get("CoreNetworkArn")
        )
        gateway = route.get("GatewayId") or ""
        return target or (gateway if gateway.startswith("vgw-") else None)

    def _egress_of(self, table):
        for route in table.get("Routes", []):
            target = self._egress_target(route)
            if target:
                return target
        return None

    def _check_their_egress(self):
        """Every zone the nodes run in, not whichever table happens to leave.

        With tables named per AZ ours use those; with none named the module detects
        the table their own subnets in that zone use. Either way a zone whose table
        cannot reach the registry is a zone whose nodes cannot pull an image, and it
        has to fail here rather than an hour later.
        """
        named = self.route_table_ids or {}
        try:
            by_zone = {
                az: (
                    self.ec2.describe_route_tables(RouteTableIds=[named[az]])["RouteTables"]
                    if az in named
                    else self._their_tables_in(az)
                )
                for az in self.azs
            }
        except Exception as e:  # noqa: BLE001 - reported as a failed check
            self._add_result("Subnet Egress", False, "Failed to check", str(e))
            return

        egressing, silent, ambiguous = {}, [], {}
        for az, tables in by_zone.items():
            leaving = {t["RouteTableId"]: self._egress_of(t) for t in tables}
            leaving = {k: v for k, v in leaving.items() if v}
            if az not in named and len(leaving) > 1:
                # detection refuses to guess between them, so this deploy would stop
                ambiguous[az] = ", ".join(f"{k} via {v}" for k, v in sorted(leaving.items()))
                continue
            if not leaving and az not in named:
                # our subnets are associated with nothing, so what they inherit is the
                # main table - which egresses in a VPC whose own subnets are public
                inherited = {t["RouteTableId"]: self._egress_of(t) for t in self._main_table()}
                leaving = {k: f"{v} inherited" for k, v in inherited.items() if v}
            if leaving:
                egressing[az] = ", ".join(f"{k} via {v}" for k, v in sorted(leaving.items()))
            else:
                silent.append(az)

        if ambiguous:
            self._add_result(
                "Subnet Egress",
                False,
                "; ".join(f"{az}: {v}" for az, v in sorted(ambiguous.items())),
                "More than one route table egresses from that zone, and detection will "
                "not choose between them. Name the one to use per availability zone in "
                "PINECONE_ROUTE_TABLE_IDS",
            )
            return
        if silent:
            self._add_result(
                "Subnet Egress",
                False,
                f"nothing egresses from {', '.join(sorted(silent))}",
                "EKS nodes reach the registry through the customer's NAT, transit "
                "gateway, virtual private gateway or firewall, and every zone they run "
                "in needs one; a proxy or VPC endpoints instead of a default route is "
                "also workable, in which case this check is safe to ignore",
            )
            return
        self._add_result(
            "Subnet Egress", True, "; ".join(f"{az}: {v}" for az, v in sorted(egressing.items()))
        )

    def _their_tables_in(self, az: str):
        """The tables their own subnets in that zone use."""
        subnets = self.ec2.describe_subnets(
            Filters=[
                {"Name": "vpc-id", "Values": [self.vpc_id]},
                {"Name": "availability-zone", "Values": [az]},
            ]
        )["Subnets"]
        ids = [s["SubnetId"] for s in subnets]
        if not ids:
            return []
        return self.ec2.describe_route_tables(
            Filters=[{"Name": "association.subnet-id", "Values": ids}]
        )["RouteTables"]

    def _main_table(self):
        return self.ec2.describe_route_tables(
            Filters=[
                {"Name": "vpc-id", "Values": [self.vpc_id]},
                {"Name": "association.main", "Values": ["true"]},
            ]
        )["RouteTables"]

    def _check_igw_attached(self):
        try:
            gateways = self.ec2.describe_internet_gateways(
                Filters=[{"Name": "attachment.vpc-id", "Values": [self.vpc_id]}]
            )["InternetGateways"]
        except Exception as e:  # noqa: BLE001 - reported as a failed check
            self._add_result("Internet Gateway", False, "Failed to check", str(e))
            return
        if gateways:
            self._add_result(
                "Internet Gateway", True, f"{gateways[0]['InternetGatewayId']} is attached"
            )
            return
        self._add_result(
            "Internet Gateway",
            False,
            f"No internet gateway attached to {self.vpc_id}",
            "An internet-facing load balancer needs one; attach it, or deploy with "
            "public access disabled to reach the data plane over PrivateLink",
        )

    def _refused(self, name: str, call, **kwargs) -> str | None:
        """The action's name if it was refused, nothing if allowed or unanswered.

        A dry run creates nothing and evaluates the whole policy chain, SCP included.
        Only UnauthorizedOperation is a refusal: a throttle or a rejected argument
        leaves the question open, and must not be why a deploy is stopped.
        """
        try:
            call(DryRun=True, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the code is the answer, whatever the class
            if getattr(exc, "response", {}).get("Error", {}).get("Code") == "UnauthorizedOperation":
                return name
        return None

    def _check_permissions(self):
        """Whether this credential may build in a VPC someone else's policies cover.

        A role scoped to their own networking is denied our subnets long before it is
        denied our cluster, and the deploy would find out an hour in.
        """
        try:
            ipaddress.ip_network(self.cidr)
        except ValueError:
            self._add_result("VPC Permissions", True, "not checked: the range is not a CIDR")
            return

        # a policy conditioned on tags refuses a probe that does not carry them, and
        # the ones the module adds at deploy time are not known here
        tag_spec = (
            [
                {
                    "ResourceType": "subnet",
                    "Tags": [{"Key": k, "Value": v} for k, v in self.tags.items()],
                }
            ]
            if self.tags
            else []
        )
        probes = [
            (
                "ec2:CreateSubnet",
                self.ec2.create_subnet,
                {
                    "VpcId": self.vpc_id,
                    "CidrBlock": self.cidr,
                    "AvailabilityZone": self.azs[0],
                    "TagSpecifications": tag_spec,
                },
            ),
            (
                "ec2:CreateSecurityGroup",
                self.ec2.create_security_group,
                {
                    "VpcId": self.vpc_id,
                    "GroupName": "pinecone-preflight-lb-backend-sg",
                    "Description": "Shared backend security group for load balancers",
                },
            ),
        ]
        if self.public_access:
            probes.append(
                ("ec2:CreateRouteTable", self.ec2.create_route_table, {"VpcId": self.vpc_id})
            )
        for table_id in sorted(set((self.route_table_ids or {}).values())):
            probes.append(
                (
                    f"ec2:AssociateRouteTable on {table_id}",
                    self.ec2.associate_route_table,
                    # no subnet of ours exists yet; authorization is decided before the
                    # lookup, so a scoped policy answers and a NotFound reads as unknown
                    {"RouteTableId": table_id, "SubnetId": "subnet-" + "0" * 17},
                )
            )

        refused = [name for name, call, kwargs in probes if self._refused(name, call, **kwargs)]
        if not refused:
            self._add_result("VPC Permissions", True, f"the layout is permitted in {self.vpc_id}")
            return
        self._add_result(
            "VPC Permissions",
            False,
            f"{', '.join(refused)} refused",
            f"Grant these on {self.vpc_id}, or leave the VPC blank to deploy into one of "
            "our own. Not covered either way: ec2:AssociateVpcCidrBlock and "
            "ec2:ModifySubnetAttribute have no dry run, and a policy conditioned on the "
            "tags the module adds at deploy time cannot be answered from here.",
        )

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

    def _laid_out_range(self):
        """The range as a network, or None having said why it is not one we can use.

        Whose VPC it goes in does not change what the layout needs of it, so both
        shapes ask this before asking anything of the cloud.
        """
        try:
            target_net = ipaddress.ip_network(self.cidr)
        except ValueError:
            self._add_result(
                "VPC CIDR",
                False,
                f"Invalid CIDR: {self.cidr}",
                "Enter a valid CIDR block (e.g., 10.0.0.0/16)",
            )
            return None

        if not MIN_VPC_PREFIX <= target_net.prefixlen <= MAX_VPC_PREFIX:
            self._add_result(
                "VPC CIDR",
                False,
                f"/{target_net.prefixlen} is outside what is currently supported",
                f"A /{MIN_VPC_PREFIX} to /{MAX_VPC_PREFIX} is supported today "
                f"(e.g. 10.0.0.0/{MIN_VPC_PREFIX} or 192.168.16.0/{MAX_VPC_PREFIX}); "
                f"/{MAX_VPC_PREFIX} is the smallest range the subnet layout fits in",
            )
            return None

        if len(self.azs) > MAX_AZS:
            self._add_result(
                "VPC CIDR",
                False,
                f"{len(self.azs)} availability zones is more than the layout fits",
                f"The range is cut into sixteen slots and {MAX_AZS} zones fill them; "
                "deploy into three or fewer",
            )
            return None

        # AWS rejects CIDRs in 100.64.0.0/10 and other reserved ranges
        if not any(target_net.subnet_of(block) for block in RFC1918_RANGES):
            self._add_result(
                "VPC CIDR",
                False,
                f"{self.cidr} is not in an RFC 1918 private range",
                "Use a block inside 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16. "
                "See https://docs.aws.amazon.com/vpc/latest/userguide/vpc-cidr-blocks.html",
            )
            return None
        return target_net

    def _check_cidr_conflicts(self):
        target_net = self._laid_out_range()
        if target_net is None:
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
                self.non_interactive or len(conflicts) == 0,
                f"{self.cidr} available"
                if not conflicts
                else f"Conflicts with: {', '.join(conflicts)}",
                "Choose a different CIDR range to avoid conflicts" if conflicts else None,
            )
        except Exception as e:
            self._add_result("VPC CIDR", False, "Failed to check", str(e))


class AWSSetupWizard(BaseSetupWizard):
    CONTROL_PLANE_KEYS = ("global-env", "api-url", "auth0-domain", "gcp-project")
    TOTAL_STEPS = 16
    HEADER_TITLE = "Pinecone BYOC Setup Wizard"
    HEADER_SUBTITLE = "This wizard will set up everything you need to deploy Pinecone BYOC."
    DEFAULT_CIDR = "10.0.0.0/20"
    CIDR_DESC = "The IP range for your VPC (a /16 to /20 from an RFC 1918 private range, currently supported down to /20, must not conflict with existing VPCs)"
    DELETION_PROTECTION_DESC = "Protect RDS databases and S3 buckets from accidental deletion"
    PRIVATE_ACCESS_DESC = "Private access requires AWS PrivateLink (more secure)"
    METADATA_NAME = "tags"
    CLOUD_NAME = "AWS"

    def run(self, output_dir: str = ".") -> bool:
        api_key = self._validated_api_key(output_dir)
        if not api_key:
            return False

        if not self._validate_aws_creds():
            return False

        region = self._get_region()
        azs = self._get_azs(region)
        custom_ami_id = self._get_custom_ami_id()
        kms_key_arn = self._get_kms_key_arn()
        vpc_id = self._get_existing_vpc(region)
        if vpc_id:
            self.TOTAL_STEPS += 1  # egress, asked only of a VPC we did not create
        route_table_ids = self._get_route_table_ids(region, vpc_id, azs)
        cidr = self._get_cidr(vpc_id, region)
        deletion_protection = self._get_deletion_protection()
        public_access = self._get_public_access(vpc_id=vpc_id, region=region)
        tags = self._get_custom_metadata()

        if not self._destroy and not self._run_preflight_checks(
            region, azs, cidr, vpc_id, route_table_ids, public_access, tags
        ):
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
            vpc_id=vpc_id,
            route_table_ids=route_table_ids,
            control_plane=self._control_plane_overrides(),
        )

    def _select_aws_profile(self) -> None:
        import boto3

        if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
            console.print(
                "  [dim]Using credentials from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY[/]"
            )
            return

        default = os.environ.get("AWS_PROFILE") or "default"
        if self._non_interactive:
            console.print(f"  [dim]Using AWS profile {default}[/]")
            return

        available = boto3.Session().available_profiles
        options = [default] + [p for p in available if p != default]

        for line in self._in_columns(options):
            console.print(f"    [dim]{line}[/]")
        console.print()
        console.print("  [dim]Tab cycles through configured profiles; Enter to accept[/]")
        profile = self._prompt("AWS profile", default, key="AWS_PROFILE", options=options)
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

    def _fetch_vpcs(self, region: str) -> list[tuple[str, str, str]]:
        import boto3

        client = boto3.Session().client("ec2", region_name=region)
        found = []
        for vpc in client.describe_vpcs().get("Vpcs", []):
            name = next(
                (t["Value"] for t in vpc.get("Tags", []) if t["Key"] == "Name"),
                "",
            )
            found.append((vpc["VpcId"], vpc.get("CidrBlock", ""), name))
        return found

    def _fetch_vpc_cidrs(self, region: str, vpc_id: str) -> list[str]:
        import boto3

        client = boto3.Session().client("ec2", region_name=region)
        vpcs = client.describe_vpcs(VpcIds=[vpc_id]).get("Vpcs", [])
        if not vpcs:
            return []
        return [
            association["CidrBlock"]
            for association in vpcs[0].get("CidrBlockAssociationSet", [])
            if association.get("CidrBlockState", {}).get("State") == "associated"
        ]

    def _get_existing_vpc(self, region: str) -> str | None:
        console.print()
        console.print(f"  {self._step('Existing VPC')}")
        console.print("  [dim]Deploy into a VPC you already have, or leave blank to")
        console.print("  [dim]let the module create one[/]")
        console.print()

        options = None
        if not self._non_interactive:
            try:
                found = self._fetch_vpcs(region)
                options = [vpc_id for vpc_id, _, _ in found]
                for vpc_id, cidr, name in found:
                    console.print(f"    [dim]{vpc_id}  {cidr}  {name}[/]")
            except Exception as exc:  # noqa: BLE001 - listing is a convenience
                console.print(f"  [dim]Could not list VPCs: {exc}[/]")

        vpc_id = self._prompt(
            "Enter VPC id (blank to create one)",
            "",
            key="PINECONE_EXISTING_VPC_ID",
            options=options,
        ).strip()
        return vpc_id or None

    def _detect_route_tables(self, region: str, vpc_id: str, azs: list[str]):
        """Per zone, the tables their own subnets there use and what each leaves by.

        The same question the module asks itself at deploy time, asked early so the
        answer can be shown rather than demanded.
        """
        import boto3

        client = boto3.Session().client("ec2", region_name=region)

        def leaves_by(table):
            """The default route's target, and whether a private subnet can use it.

            An internet gateway carries 0.0.0.0/0 and still leaves a private subnet
            with no way out, and a peering connection routes nothing transitively.
            Both are worth naming rather than reporting the zone as empty.
            """
            for route in table.get("Routes", []):
                if route.get("DestinationCidrBlock") != "0.0.0.0/0":
                    continue
                if route.get("State") != "active":
                    continue
                target = (
                    route.get("NatGatewayId")
                    or route.get("TransitGatewayId")
                    or route.get("VpcEndpointId")
                    or route.get("NetworkInterfaceId")
                    or route.get("InstanceId")
                    or route.get("CoreNetworkArn")
                )
                gateway = route.get("GatewayId") or ""
                target = target or (gateway if gateway.startswith("vgw-") else None)
                if target:
                    return target, True
                unusable = gateway or route.get("VpcPeeringConnectionId")
                if unusable:
                    return unusable, False
            return None, False

        main = client.describe_route_tables(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "association.main", "Values": ["true"]},
            ]
        )["RouteTables"]

        found = {}
        for az in azs:
            subnets = client.describe_subnets(
                Filters=[
                    {"Name": "vpc-id", "Values": [vpc_id]},
                    {"Name": "availability-zone", "Values": [az]},
                ]
            )["Subnets"]
            ids = [s["SubnetId"] for s in subnets]
            tables = (
                client.describe_route_tables(
                    Filters=[{"Name": "association.subnet-id", "Values": ids}]
                )["RouteTables"]
                if ids
                else []
            )
            seen = {
                table["RouteTableId"]: (*leaves_by(table), False)
                for table in tables
                if leaves_by(table)[0] is not None
            }
            if not any(usable for _, usable, _ in seen.values()):
                # a subnet we associate with nothing inherits the main table
                seen |= {
                    table["RouteTableId"]: (*leaves_by(table), True)
                    for table in main
                    if leaves_by(table)[1]
                }
            found[az] = seen
        return found

    def _get_route_table_ids(
        self, region: str, vpc_id: str | None, azs: list[str]
    ) -> dict[str, str] | None:
        if not vpc_id:
            return None
        if self._non_interactive:
            return self._parse_route_table_ids(os.environ.get("PINECONE_ROUTE_TABLE_IDS", ""))

        try:
            found = self._detect_route_tables(region, vpc_id, azs)
        except Exception as exc:  # noqa: BLE001 - the prompt still stands without it
            console.print(f"  [dim]Could not read route tables: {exc}[/]")
            found = dict.fromkeys(azs, {})

        console.print()
        console.print(f"  {self._step('Egress')}")
        console.print("  [dim]The route table each zone's nodes leave by: the one carrying")
        console.print("  [dim]0.0.0.0/0 to your NAT, transit gateway or firewall[/]")

        chosen = {}
        for az in azs:
            seen = found.get(az, {})
            console.print()
            for table_id, (target, usable, inherited) in seen.items():
                why = (
                    "  inherited, leave blank to keep it"
                    if inherited
                    else ""
                    if usable
                    else "  a private subnet cannot use this"
                )
                console.print(f"    [dim]{table_id}  0.0.0.0/0 -> {target}{why}[/]")
            if not seen:
                console.print(f"    [dim]nothing in {az} carries 0.0.0.0/0[/]")
            offer = [t for t, (_, usable, inherited) in seen.items() if usable and not inherited]
            answer = self._prompt(
                f"Route table for {az}",
                next(iter(offer), ""),
                options=offer or None,
            ).strip()
            if answer:
                chosen[az] = answer
        return chosen or None

    @staticmethod
    def _parse_route_table_ids(value: str) -> dict[str, str] | None:
        entries = [entry for entry in value.replace(" ", "").split(",") if entry]
        parsed = {}
        for entry in entries:
            az, _, route_table_id = entry.partition("=")
            if not az or not route_table_id.startswith("rtb-"):
                raise ValueError(f"PINECONE_ROUTE_TABLE_IDS entry {entry!r} is not <az>=rtb-<id>")
            parsed[az] = route_table_id
        return parsed or None

    @staticmethod
    def _suggest_cidr(vpc_cidrs: list[str]) -> str | None:
        """A free /16 from the RFC 1918 block their VPC already numbers from.

        A range they carry is where their own subnets live, and one from another
        block cannot be associated at all: AWS refuses a secondary CIDR outside the
        primary's RFC 1918 range. Nothing free there is nothing to suggest.
        """
        used = []
        for cidr in vpc_cidrs:
            try:
                used.append(ipaddress.ip_network(cidr))
            except ValueError:
                continue
        for block in RFC1918_RANGES:
            if not any(net.subnet_of(block) for net in used):
                continue
            if block.prefixlen > MIN_VPC_PREFIX:
                continue
            for candidate in block.subnets(new_prefix=MIN_VPC_PREFIX):
                if not any(candidate.overlaps(net) for net in used):
                    return str(candidate)
        return None

    def _internet_gateway(self, region: str, vpc_id: str) -> str | None:
        import boto3

        client = boto3.Session().client("ec2", region_name=region)
        found = client.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
        )["InternetGateways"]
        return found[0]["InternetGatewayId"] if found else None

    def _get_public_access(
        self,
        default: str = "Y",
        note: str = "",
        vpc_id: str | None = None,
        region: str | None = None,
    ) -> bool:
        """An internet-facing load balancer needs an internet gateway in their VPC.

        Offering public access by default in a VPC that has none is offering a
        deployment that cannot come up, so ask the VPC first and let the answer pick
        the default.
        """
        if vpc_id and region and not self._non_interactive:
            with contextlib.suppress(Exception):
                gateway = self._internet_gateway(region, vpc_id)
                note = (
                    f"{gateway} is attached, so a public load balancer can go in {vpc_id}"
                    if gateway
                    else f"{vpc_id} has no internet gateway; a public load balancer needs one"
                )
                default = "Y" if gateway else "n"
        return super()._get_public_access(default, note)

    def _get_cidr(self, vpc_id: str | None = None, region: str | None = None) -> str:
        if vpc_id and region and not self._non_interactive:
            with contextlib.suppress(Exception):
                suggested = self._suggest_cidr(self._fetch_vpc_cidrs(region, vpc_id))
                if suggested:
                    self.DEFAULT_CIDR = suggested
        if self._non_interactive and self._non_interactive_cidr() is None:
            raise NonInteractiveInputRequired("VPC CIDR block", "PINECONE_VPC_CIDR")
        return super()._get_cidr()

    def _get_region(self) -> str:
        console.print()
        console.print(f"  {self._step('AWS Region')}")
        console.print()
        return self._prompt("Enter AWS region", "us-east-1", key="PINECONE_REGION")

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
            "Enter AZs (comma-separated)",
            self._zone_default("PINECONE_AZS", available),
            key="PINECONE_AZS",
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
        ami_id = self._prompt(
            "Enter AMI ID (or press Enter to skip)",
            "",
            key="PINECONE_CUSTOM_AMI_ID",
        )
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
        arn = self._prompt(
            "Enter KMS key ARN (or press Enter to skip)",
            "",
            key="PINECONE_KMS_KEY_ARN",
        )
        return arn or None

    def _run_preflight_checks(
        self,
        region: str,
        azs: list[str],
        cidr: str,
        vpc_id: str | None = None,
        route_table_ids: dict[str, str] | None = None,
        public_access: bool = True,
        tags: dict[str, str] | None = None,
    ) -> bool:
        console.print()
        console.print(f"  {self._step('Preflight Checks')}")
        console.print()

        checker = AWSPreflightChecker(
            region,
            azs,
            cidr,
            vpc_id=vpc_id,
            route_table_ids=route_table_ids,
            public_access=public_access,
            tags=tags,
            non_interactive=self._non_interactive,
        )
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
        vpc_id: str | None = None,
        route_table_ids: dict[str, str] | None = None,
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
        existing_vpc_id=config.get("existing-vpc-id"),
        existing_route_table_ids=config.get_object("existing-route-table-ids"),
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

        # the VPC to deploy into, and which of their route tables our subnets use
        if vpc_id:
            config_content += f"  {project_name}:existing-vpc-id: {vpc_id}\n"
        if route_table_ids:
            config_content += f"  {project_name}:existing-route-table-ids:\n"
            for az, route_table_id in route_table_ids.items():
                config_content += f"    {az}: {route_table_id}\n"

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
    def __init__(
        self,
        project_id: str,
        region: str,
        zones: list[str],
        cidr: str,
        non_interactive: bool = False,
    ):
        self.non_interactive = non_interactive
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
                    self.non_interactive,
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
        api_key = self._validated_api_key(output_dir)
        if not api_key:
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

        if not self._destroy and not self._run_preflight_checks(project_id, region, zones, cidr):
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
        return self._prompt("Enter GCP project ID", detected_project, key="GCP_PROJECT")

    def _get_region(self) -> str:
        console.print()
        console.print(f"  {self._step('GCP Region')}")
        console.print()
        return self._prompt("Enter GCP region", "us-central1", key="PINECONE_REGION")

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
            "Enter zones (comma-separated)",
            self._zone_default("PINECONE_AZS", available),
            key="PINECONE_AZS",
        )
        zones = [zone.strip() for zone in zones_input.split(",")]
        return zones

    def _run_preflight_checks(
        self, project_id: str, region: str, zones: list[str], cidr: str
    ) -> bool:
        console.print()
        console.print(f"  {self._step('Preflight Checks')}")
        console.print()

        checker = GCPPreflightChecker(
            project_id, region, zones, cidr, non_interactive=self._non_interactive
        )
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
    def __init__(
        self,
        subscription_id: str,
        region: str,
        zones: list[str],
        cidr: str,
        non_interactive: bool = False,
    ):
        self.non_interactive = non_interactive
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
                    self.non_interactive,
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
        api_key = self._validated_api_key(output_dir)
        if not api_key:
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

        if not self._destroy and not self._run_preflight_checks(
            subscription_id, region, zones, cidr
        ):
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
            "Enter Azure subscription ID",
            detected_subscription,
            key="AZURE_SUBSCRIPTION_ID",
        )

    def _get_region(self) -> str:
        console.print()
        console.print(f"  {self._step('Azure Region')}")
        console.print()
        return self._prompt("Enter Azure region", "eastus", key="PINECONE_REGION")

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
            "Enter zones (comma-separated)",
            self._zone_default("PINECONE_AZS", available),
            key="PINECONE_AZS",
        )
        zones = [zone.strip() for zone in zones_input.split(",")]
        return zones

    def _run_preflight_checks(
        self, subscription_id: str, region: str, zones: list[str], cidr: str
    ) -> bool:
        console.print()
        console.print(f"  {self._step('Preflight Checks')}")
        console.print()

        checker = AzurePreflightChecker(
            subscription_id, region, zones, cidr, non_interactive=self._non_interactive
        )
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


def run_setup(
    output_dir: str = ".",
    cloud: str | None = None,
    non_interactive: bool = False,
    stack_name: str = "prod",
    skip_install: bool = False,
    dev_source: str | None = None,
    destroy: bool = False,
) -> bool:
    try:
        if not cloud:
            if non_interactive:
                console.print("  [red]✗[/] --cloud is required in non-interactive mode")
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
            non_interactive=non_interactive,
            stack_name=stack_name,
            skip_install=skip_install,
            dev_source=dev_source,
            destroy=destroy,
        )
        return wizard.run(output_dir)

    except KeyboardInterrupt:
        console.print()
        console.print("  [yellow]Setup cancelled by user[/]")
        return False
    except NonInteractiveInputRequired as e:
        console.print()
        console.print(f"  [red]✗[/] --non-interactive cannot prompt for: {e.field}")
        wanted = e.env_var or "it"
        console.print(
            f"  [dim]Set {wanted} in the environment, or drop --non-interactive to be asked.[/]"
        )
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
        "--non-interactive",
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
        "--destroy",
        action="store_true",
        help="Regenerate a project in order to tear it down. Skips the preflight checks, "
        "which ask whether there is room to create what this stack already occupies.",
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

    success = run_setup(
        args.output_dir,
        args.cloud,
        non_interactive=args.non_interactive,
        stack_name=args.stack_name,
        skip_install=args.skip_install,
        dev_source=dev_source,
        destroy=args.destroy,
    )
    sys.exit(0 if success else 1)
