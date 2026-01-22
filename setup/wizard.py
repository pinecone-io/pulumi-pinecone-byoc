"""
Pinecone BYOC Setup Wizard.

Interactive setup that creates a complete Pulumi project for BYOC deployment.
"""

import os
import sys
import tty
import termios
from dataclasses import dataclass
from typing import Optional

import boto3
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.status import Status


# pinecone blue
BLUE = "#002BFF"

console = Console()


def _read_input_with_placeholder(
    prompt: str, placeholder: str = "", password: bool = False
) -> str:
    """Read input with a dimmed placeholder that disappears when typing."""
    console.print(f"  {prompt}: ", end="")

    # open /dev/tty directly to handle curl pipe case where stdin is not a TTY
    # use binary mode with no buffering to avoid input lag
    tty_file = open("/dev/tty", "rb", buffering=0)
    fd = tty_file.fileno()
    old_settings = termios.tcgetattr(fd)

    def show_placeholder():
        if placeholder and not password:
            sys.stdout.write(f"\033[2m{placeholder}\033[0m")  # dim
            sys.stdout.write(f"\033[{len(placeholder)}D")  # move back
            sys.stdout.flush()

    def clear_placeholder():
        if placeholder and not password:
            sys.stdout.write(" " * len(placeholder))
            sys.stdout.write(f"\033[{len(placeholder)}D")
            sys.stdout.flush()

    show_placeholder()

    try:
        tty.setraw(fd)
        result = []
        placeholder_visible = True

        while True:
            char = tty_file.read(1).decode("utf-8", errors="replace")

            # enter - accept
            if char in ("\r", "\n"):
                if not result and placeholder:
                    result = list(placeholder)
                break

            # tab or right arrow - complete with placeholder
            if char == "\t" or char == "\x1b":
                if char == "\x1b":
                    # read arrow key sequence
                    next1 = tty_file.read(1).decode("utf-8", errors="replace")
                    next2 = tty_file.read(1).decode("utf-8", errors="replace")
                    if next1 == "[" and next2 == "C":  # right arrow
                        if placeholder and not result:
                            clear_placeholder()
                            result = list(placeholder)
                            sys.stdout.write(placeholder)
                            sys.stdout.flush()
                            placeholder_visible = False
                    continue
                else:  # tab
                    if placeholder and not result:
                        clear_placeholder()
                        result = list(placeholder)
                        sys.stdout.write(placeholder)
                        sys.stdout.flush()
                        placeholder_visible = False
                    continue

            # backspace
            if char in ("\x7f", "\x08"):
                if result:
                    result.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                    # if empty, show placeholder again
                    if not result and placeholder and not password:
                        show_placeholder()
                        placeholder_visible = True
                continue

            # ctrl+c
            if char == "\x03":
                raise KeyboardInterrupt

            # ctrl+d
            if char == "\x04":
                if not result:
                    raise EOFError
                continue

            # ignore other control chars
            if ord(char) < 32:
                continue

            # clear placeholder on first real char
            if placeholder_visible and placeholder and not password:
                clear_placeholder()
                placeholder_visible = False

            result.append(char)
            sys.stdout.write("•" if password else char)
            sys.stdout.flush()

        return "".join(result)

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        tty_file.close()
        console.print()


@dataclass
class PreflightResult:
    """Result of a single preflight check."""

    name: str
    passed: bool
    message: str
    details: Optional[str] = None


class SetupWizard:
    """Interactive setup wizard for Pinecone BYOC deployment."""

    def __init__(self):
        self.results: list[PreflightResult] = []

    def run(self, output_dir: str = ".") -> bool:
        """Run the interactive setup wizard."""
        self._print_header()

        # step 1: get pinecone api key
        api_key = self._get_api_key()
        if not api_key:
            return False

        # step 2: validate api key
        if not self._validate_api_key(api_key):
            return False

        # step 3: validate aws credentials (early - we need them for everything else)
        if not self._validate_aws_creds():
            return False

        # step 4: get region
        region = self._get_region()

        # step 5: get availability zones
        azs = self._get_azs(region)

        # step 6: get vpc cidr
        cidr = self._get_cidr()

        # step 7: get deletion protection preference
        deletion_protection = self._get_deletion_protection()

        # step 8: run preflight checks
        if not self._run_preflight_checks(region, azs, cidr):
            return False

        # step 9: get project name
        project_name = self._get_project_name()

        # step 10: set up pulumi backend
        if not self._setup_pulumi_backend():
            return False

        # step 11: generate everything
        return self._generate_project(
            output_dir, project_name, api_key, region, azs, cidr, deletion_protection
        )

    def _print_header(self):
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

    def _prompt(
        self, message: str, default: Optional[str] = None, password: bool = False
    ) -> str:
        """Prompt for input with optional default shown as placeholder."""
        return _read_input_with_placeholder(message, default or "", password)

    def _get_api_key(self) -> Optional[str]:
        """Get Pinecone API key from user."""
        console.print()
        console.print(f"  [{BLUE}]Step 1/10[/] · Pinecone API Key")
        console.print("  [dim]Find your key at app.pinecone.io[/]")
        console.print()

        env_key = os.environ.get("PINECONE_API_KEY")
        if env_key:
            use_env = self._prompt(
                "Found PINECONE_API_KEY in environment. Use it?", "y"
            )
            if use_env.lower() == "y":
                return env_key

        api_key = self._prompt("Enter your Pinecone API key", password=True)
        if not api_key:
            console.print("\n  [red]✗[/] API key is required")
            return None

        return api_key

    def _validate_api_key(self, api_key: str) -> bool:
        """Validate the Pinecone API key by calling the API."""
        console.print()
        console.print(f"  [{BLUE}]Step 2/10[/] · Validating API Key")
        console.print()

        import urllib.error
        import urllib.request

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

    def _validate_aws_creds(self) -> bool:
        """Validate AWS credentials early - we need them for everything else."""
        console.print()
        console.print(f"  [{BLUE}]Step 3/10[/] · AWS Credentials")
        console.print()

        with Status(
            "  [dim]Validating AWS credentials...[/]", console=console, spinner="dots"
        ):
            try:
                sts = boto3.client("sts")
                identity = sts.get_caller_identity()
                account_id = identity["Account"]
            except Exception as e:
                console.print(f"  [red]✗[/] AWS credentials invalid: {e}")
                console.print()
                console.print(
                    "  [dim]Make sure you have valid AWS credentials configured.[/]"
                )
                console.print("  [dim]You can set them via:[/]")
                console.print(
                    "    [dim]· AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY[/]"
                )
                console.print("    [dim]· aws configure[/]")
                console.print("    [dim]· AWS_PROFILE environment variable[/]")
                return False

        console.print(
            f"  [green]✓[/] AWS credentials valid [dim](Account: {account_id})[/]"
        )
        return True

    def _get_region(self) -> str:
        """Get AWS region from user."""
        console.print()
        console.print(f"  [{BLUE}]Step 4/10[/] · AWS Region")
        console.print()
        return self._prompt("Enter AWS region", "us-east-1")

    def _fetch_azs(self, region: str) -> list[str]:
        """Fetch available AZs from AWS for the given region."""
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
        """Get availability zones from user."""
        console.print()
        console.print(f"  [{BLUE}]Step 5/10[/] · Availability Zones")
        console.print()

        with Status(
            "  [dim]Fetching availability zones...[/]", console=console, spinner="dots"
        ):
            available = self._fetch_azs(region)

        console.print(f"  [dim]Available in {region}:[/] {', '.join(available)}")
        default_azs = available[:2]

        azs_input = self._prompt("Enter AZs (comma-separated)", ",".join(default_azs))
        azs = [az.strip() for az in azs_input.split(",")]
        return azs

    def _get_cidr(self) -> str:
        """Get VPC CIDR from user."""
        console.print()
        console.print(f"  [{BLUE}]Step 6/10[/] · VPC CIDR Block")
        console.print(
            "  [dim]The IP range for your VPC (must not conflict with existing VPCs)[/]"
        )
        console.print()
        return self._prompt("Enter CIDR block", "10.0.0.0/16")

    def _get_deletion_protection(self) -> bool:
        """Get deletion protection preference from user."""
        console.print()
        console.print(f"  [{BLUE}]Step 7/10[/] · Deletion Protection")
        console.print(
            "  [dim]Protect RDS databases and S3 buckets from accidental deletion[/]"
        )
        console.print()
        response = self._prompt("Enable deletion protection? (Y/n)", "Y")
        return response.lower() in ("y", "yes", "")

    def _run_preflight_checks(self, region: str, azs: list[str], cidr: str) -> bool:
        """Run preflight checks for AWS environment."""
        console.print()
        console.print(f"  [{BLUE}]Step 8/10[/] · Preflight Checks")
        console.print()

        checker = PreflightChecker(region, azs, cidr)
        if not checker.run_checks():
            console.print()
            console.print(
                "  [red]Preflight checks failed. Fix the issues above before proceeding.[/]"
            )
            return False

        return True

    def _get_project_name(self) -> str:
        """Get project name from user."""
        console.print()
        console.print(f"  [{BLUE}]Step 9/11[/] · Project Name")
        console.print(
            "  [dim]A short name for this deployment (e.g., 'pinecone-prod')[/]"
        )
        console.print()
        return self._prompt("Enter project name", "pinecone-byoc")

    def _setup_pulumi_backend(self) -> bool:
        """Set up Pulumi backend (local or cloud)."""
        import subprocess

        console.print()
        console.print(f"  [{BLUE}]Step 10/11[/] · Pulumi Backend")
        console.print("  [dim]Where to store infrastructure state[/]")
        console.print()

        backend = self._prompt("Backend (local/cloud)", "local").lower()
        use_local = backend != "cloud"

        if use_local:
            console.print()
            console.print(
                "  [dim]Enter a passphrase to encrypt secrets (remember this!)[/]"
            )
            passphrase = self._prompt("Passphrase", password=True)
            if not passphrase:
                console.print("  [red]✗[/] Passphrase is required for local backend")
                return False

            # set env var for subsequent pulumi commands
            os.environ["PULUMI_CONFIG_PASSPHRASE"] = passphrase

            # login to local backend
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
        """Check if pulumi CLI is installed."""
        import shutil

        return shutil.which("pulumi") is not None

    def _generate_project(
        self,
        output_dir: str,
        project_name: str,
        api_key: str,
        region: str,
        azs: list[str],
        cidr: str,
        deletion_protection: bool,
    ):
        """Generate complete Pulumi project."""
        console.print()
        console.print(f"  [{BLUE}]Step 11/11[/] · Creating Project")
        console.print()

        if not self._check_pulumi_installed():
            console.print("  [red]✗[/] Pulumi CLI not found")
            console.print(
                "  [dim]Install Pulumi first:[/] https://www.pulumi.com/docs/install/"
            )
            return False

        import subprocess

        # create Pulumi.yaml
        pulumi_yaml = {
            "name": project_name,
            "runtime": {
                "name": "python",
                "options": {"virtualenv": ".venv", "toolchain": "uv"},
            },
            "description": "Pinecone BYOC deployment",
        }

        pulumi_yaml_path = os.path.join(output_dir, "Pulumi.yaml")
        with open(pulumi_yaml_path, "w") as f:
            yaml.dump(pulumi_yaml, f, default_flow_style=False)
        console.print("  [green]✓[/] Created Pulumi.yaml")

        # create __main__.py
        main_py = '''"""Pinecone BYOC deployment."""

import pulumi
from pulumi_pinecone_byoc import PineconeAWSCluster, PineconeAWSClusterArgs

config = pulumi.Config()

cluster = PineconeAWSCluster(
    name="pinecone-aws-cluster",
    args=PineconeAWSClusterArgs(
        pinecone_api_key=config.require_secret("pinecone_api_key"),
        region=config.require("region"),
        vpc_cidr=config.get("vpc_cidr"),
        availability_zones=config.require_object("availability_zones"),
        deletion_protection=config.get_bool("deletion_protection") if config.get_bool("deletion_protection") is not None else True,
        global_env=config.get("global_env") or "dev",
    ),
)

update_kubeconfig_command = cluster.cluster_name.apply(
    lambda name: f"aws eks update-kubeconfig --region {config.require('region')} --name {name}"
)
pulumi.export("environment", cluster.environment_name)
pulumi.export("update_kubeconfig_command", update_kubeconfig_command)
'''

        main_py_path = os.path.join(output_dir, "__main__.py")
        with open(main_py_path, "w") as f:
            f.write(main_py)
        console.print("  [green]✓[/] Created __main__.py")

        # create pyproject.toml for uv toolchain to install dependencies
        pyproject_content = """[project]
name = "pinecone-byoc"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pulumi-pinecone-byoc"]
"""
        pyproject_path = os.path.join(output_dir, "pyproject.toml")
        with open(pyproject_path, "w") as f:
            f.write(pyproject_content)
        console.print("  [green]✓[/] Created pyproject.toml")

        # install dependencies with uv
        with Status(
            "  [dim]Installing dependencies...[/]", console=console, spinner="dots"
        ):
            result = subprocess.run(
                ["uv", "sync"],
                cwd=output_dir,
                capture_output=True,
                text=True,
            )

        if result.returncode == 0:
            console.print("  [green]✓[/] Dependencies installed")
        else:
            console.print(
                f"  [red]✗[/] Failed to install dependencies: {result.stderr.strip()}"
            )
            console.print("  [dim]Run manually:[/] uv sync")
            return False

        # create stack config
        stack_name = "dev"
        deletion_protection_str = str(deletion_protection).lower()
        config_content = f"""config:
  {project_name}:region: {region}
  {project_name}:vpc_cidr: {cidr}
  {project_name}:deletion_protection: {deletion_protection_str}
  {project_name}:global_env: dev
  {project_name}:availability_zones:
"""
        for az in azs:
            config_content += f"    - {az}\n"

        config_path = os.path.join(output_dir, f"Pulumi.{stack_name}.yaml")
        with open(config_path, "w") as f:
            f.write(config_content)
        console.print(f"  [green]✓[/] Created Pulumi.{stack_name}.yaml")

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
        with Status(
            "  [dim]Storing API key securely...[/]", console=console, spinner="dots"
        ):
            result = subprocess.run(
                [
                    "pulumi",
                    "config",
                    "set",
                    "--secret",
                    "pinecone_api_key",
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
            console.print(
                f"  [red]✗[/] Failed to store API key: {result.stderr.strip()}"
            )
            console.print(
                "  [dim]Run manually:[/] pulumi config set --secret pinecone_api_key <key>"
            )
            return False

        console.print("  [green]✓[/] API key stored securely")

        # print success
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
        console.print(f"    [bold {BLUE}]cd {dir_name}[/]")
        console.print(f"    [bold {BLUE}]pulumi up[/]")
        console.print()

        return True


class PreflightChecker:
    """Runs preflight checks for AWS environment."""

    def __init__(self, region: str, azs: list[str], cidr: str):
        self.region = region
        self.azs = azs
        self.cidr = cidr
        self.results: list[PreflightResult] = []

        self.ec2 = boto3.client("ec2", region_name=region)
        self.eks = boto3.client("eks", region_name=region)
        self.servicequotas = boto3.client("service-quotas", region_name=region)

    def run_checks(self) -> bool:
        """Run all preflight checks."""
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
            with Status(
                f"  [dim]Checking {name}...[/]", console=console, spinner="dots"
            ):
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

    def _add_result(
        self, name: str, passed: bool, message: str, details: Optional[str] = None
    ):
        result = PreflightResult(name, passed, message, details)
        self.results.append(result)

    def _get_quota(self, service_code: str, quota_code: str) -> Optional[float]:
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
                "Request a quota increase via AWS Service Quotas"
                if available < 1
                else None,
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
                "Request quota increase for 'EC2-VPC Elastic IPs'"
                if available < needed
                else None,
            )
        except Exception as e:
            self._add_result("Elastic IPs", False, "Failed to check", str(e))

    def _check_nat_gateway_quota(self):
        needed = len(self.azs)  # one per AZ
        quota = self._get_quota("vpc", "L-FE5A380F") or 5
        try:
            response = self.ec2.describe_nat_gateways(
                Filters=[{"Name": "state", "Values": ["available", "pending"]}]
            )
            current = len(response["NatGateways"])
            available = int(quota) - current

            self._add_result(
                "NAT Gateways",
                available >= needed,
                f"{available} available, need {needed}",
                "Request quota increase for 'NAT gateways per AZ'"
                if available < needed
                else None,
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
                "Request quota increase for 'Network Load Balancers'"
                if available < 1
                else None,
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
                "Choose different AZs or request capacity"
                if not all_available
                else None,
            )
        except Exception as e:
            self._add_result("Instance Types", False, "Failed to check", str(e))

    def _check_cidr_conflicts(self):
        # check if selected CIDR conflicts with existing VPCs
        import ipaddress

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

        try:
            response = self.ec2.describe_vpcs()
            conflicts = []
            for vpc in response["Vpcs"]:
                vpc_cidr = vpc.get("CidrBlock", "")
                if not vpc_cidr:
                    continue
                try:
                    existing_net = ipaddress.ip_network(vpc_cidr)
                    # check for actual overlap
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
                "Choose a different CIDR range to avoid conflicts"
                if conflicts
                else None,
            )
        except Exception as e:
            self._add_result("VPC CIDR", False, "Failed to check", str(e))


def run_setup(output_dir: str = ".") -> bool:
    """Run the interactive setup wizard."""
    wizard = SetupWizard()
    return wizard.run(output_dir)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pinecone BYOC Setup Wizard")
    parser.add_argument(
        "--output-dir", default=".", help="Directory to write project files"
    )
    args = parser.parse_args()

    success = run_setup(args.output_dir)
    sys.exit(0 if success else 1)
