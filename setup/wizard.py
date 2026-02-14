"""Pinecone BYOC setup wizard."""

import os
import platform
import subprocess
import sys
from dataclasses import dataclass

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.status import Status

IS_WINDOWS = platform.system() == "Windows"
if not IS_WINDOWS:
    import termios
    import tty


# pinecone blue
BLUE = "#002BFF"

console = Console()


@dataclass
class PreflightResult:
    name: str
    passed: bool
    message: str
    details: str | None = None


def _read_input_with_placeholder_unix(
    prompt: str, placeholder: str = "", password: bool = False
) -> str:
    console.print(f"  {prompt}: ", end="")

    # open /dev/tty directly to handle curl pipe case where stdin is not a TTY
    # use binary mode with no buffering to avoid input lag
    tty_file = open("/dev/tty", "rb", buffering=0)
    try:
        fd = tty_file.fileno()
        old_settings = termios.tcgetattr(fd)
    except Exception:
        tty_file.close()
        raise

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
                    if next1 == "[" and next2 == "C" and placeholder and not result:  # right arrow
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


def _read_input_with_placeholder_windows(
    prompt: str, placeholder: str = "", password: bool = False
) -> str:
    if sys.platform != "win32":
        return placeholder or ""
    import msvcrt

    console.print(f"  {prompt}: ", end="")

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

    result = []
    placeholder_visible = True

    try:
        while True:
            if msvcrt.kbhit():
                char_bytes = msvcrt.getch()

                # tab or right arrow - complete with placeholder
                if char_bytes in (b"\x00", b"\xe0"):
                    special = msvcrt.getch()
                    if (
                        char_bytes == b"\xe0" and special == b"M" and placeholder and not result
                    ):  # right arrow
                        clear_placeholder()
                        result = list(placeholder)
                        sys.stdout.write(placeholder)
                        sys.stdout.flush()
                        placeholder_visible = False
                    continue

                try:
                    char = char_bytes.decode("utf-8", errors="replace")
                except Exception:
                    continue

                # enter - accept
                if char == "\r":
                    if not result and placeholder:
                        result = list(placeholder)
                    break

                # tab or right arrow - complete with placeholder
                if char == "\t":
                    if placeholder and not result:
                        clear_placeholder()
                        result = list(placeholder)
                        sys.stdout.write(placeholder)
                        sys.stdout.flush()
                        placeholder_visible = False
                    continue

                # backspace
                if char in ("\x08", "\x7f"):
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
        console.print()


def _read_input_with_placeholder(prompt: str, placeholder: str = "", password: bool = False) -> str:
    if IS_WINDOWS:
        return _read_input_with_placeholder_windows(prompt, placeholder, password)
    else:
        return _read_input_with_placeholder_unix(prompt, placeholder, password)


# ---------------------------------------------------------------------------
# Base Setup Wizard (shared between AWS and GCP)
# ---------------------------------------------------------------------------


class BaseSetupWizard:
    TOTAL_STEPS = 13
    CLOUD_NAME: str = ""
    HEADER_TITLE: str = "Pinecone BYOC Setup Wizard"
    HEADER_SUBTITLE: str = "This wizard will set up everything you need to deploy Pinecone BYOC."
    DEFAULT_CIDR: str = "10.0.0.0/16"
    DELETION_PROTECTION_DESC: str = ""
    PRIVATE_ACCESS_DESC: str = ""
    METADATA_NAME: str = "tags"

    def __init__(
        self,
        headless: bool = False,
        stack_name: str = "prod",
        skip_install: bool = False,
    ):
        self.results: list[PreflightResult] = []
        self._current_step = 0
        self._headless = headless
        self._stack_name = stack_name
        self._skip_install = skip_install

    def _step(self, title: str) -> str:
        self._current_step += 1
        return f"[{BLUE}]Step {self._current_step}/{self.TOTAL_STEPS}[/] · {title}"

    def _prompt(self, message: str, default: str | None = None, password: bool = False) -> str:
        return _read_input_with_placeholder(message, default or "", password)

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

    def _get_cidr(self) -> str:
        console.print()
        console.print(f"  {self._step('VPC CIDR Block')}")
        console.print("  [dim]The IP range for your VPC (must not conflict with existing VPCs)[/]")
        console.print()
        return self._prompt("Enter CIDR block", self.DEFAULT_CIDR)

    def _get_deletion_protection(self) -> bool:
        console.print()
        console.print(f"  {self._step('Deletion Protection')}")
        console.print(f"  [dim]{self.DELETION_PROTECTION_DESC}[/]")
        console.print()
        response = self._prompt("Enable deletion protection? (Y/n)", "Y")
        return response.lower() in ("y", "yes", "")

    def _get_public_access(self) -> bool:
        console.print()
        console.print(f"  {self._step('Network Access')}")
        console.print("  [dim]Public access allows connections from the internet[/]")
        console.print(f"  [dim]{self.PRIVATE_ACCESS_DESC}[/]")
        console.print()
        response = self._prompt("Enable public access? (Y/n)", "Y")
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

        input_val = self._prompt(f"Enter {name} (or press Enter to skip)", "")
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
        return self._prompt("Enter project name", "pinecone-byoc")

    def _setup_pulumi_backend(self) -> bool:
        console.print()
        console.print(f"  {self._step('Pulumi Backend')}")
        console.print("  [dim]Where to store infrastructure state[/]")
        console.print()

        backend = self._prompt("Backend (local/cloud)", "local").lower()
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
        import shutil

        return shutil.which("pulumi") is not None

    def _print_success(self, output_dir: str):
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
                "Choose a different CIDR range to avoid conflicts" if conflicts else None,
            )
        except Exception as e:
            self._add_result("VPC CIDR", False, "Failed to check", str(e))


class AWSSetupWizard(BaseSetupWizard):
    TOTAL_STEPS = 14
    HEADER_TITLE = "Pinecone BYOC Setup Wizard"
    HEADER_SUBTITLE = "This wizard will set up everything you need to deploy Pinecone BYOC."
    DEFAULT_CIDR = "10.0.0.0/16"
    DELETION_PROTECTION_DESC = "Protect RDS databases and S3 buckets from accidental deletion"
    PRIVATE_ACCESS_DESC = "Private access requires AWS PrivateLink (more secure)"
    METADATA_NAME = "tags"
    CLOUD_NAME = "AWS"

    def run(self, output_dir: str = ".") -> bool:
        if self._headless:
            return self._run_headless(output_dir)

        self._print_header()

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
        )

    def _validate_aws_creds(self) -> bool:
        console.print()
        console.print(f"  {self._step('AWS Credentials')}")
        console.print()

        with Status("  [dim]Validating AWS credentials...[/]", console=console, spinner="dots"):
            try:
                import boto3

                sts = boto3.client("sts")
                identity = sts.get_caller_identity()
                account_id = identity["Account"]
            except Exception as e:
                console.print(f"  [red]✗[/] AWS credentials invalid: {e}")
                console.print()
                console.print("  [dim]Make sure you have valid AWS credentials configured.[/]")
                console.print("  [dim]You can set them via:[/]")
                console.print("    [dim]· AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY[/]")
                console.print("    [dim]· aws configure[/]")
                console.print("    [dim]· AWS_PROFILE environment variable[/]")
                return False

        console.print(f"  [green]✓[/] AWS credentials valid [dim](Account: {account_id})[/]")
        return True

    def _get_region(self) -> str:
        console.print()
        console.print(f"  {self._step('AWS Region')}")
        console.print()
        return self._prompt("Enter AWS region", "us-east-1")

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
        default_azs = available[:2]

        azs_input = self._prompt("Enter AZs (comma-separated)", ",".join(default_azs))
        azs = [az.strip() for az in azs_input.split(",")]
        return azs

    def _get_custom_ami_id(self) -> str | None:
        console.print()
        console.print(f"  {self._step('Custom AMI (Optional)')}")
        console.print("  [dim]Specify a custom AMI ID for EKS nodes (leave blank for default AWS AMI)[/]")
        console.print()
        ami_id = self._prompt("Enter AMI ID (or press Enter to skip)", "")
        return ami_id or None

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
        tags=config.get_object("tags"),
    ),
)

update_kubeconfig_command = cluster.name.apply(
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
dependencies = ["pulumi-pinecone-byoc[aws]"]
"""
        pyproject_path = os.path.join(output_dir, "pyproject.toml")
        with open(pyproject_path, "w") as f:
            f.write(pyproject_content)
        console.print("  [green]✓[/] Created pyproject.toml")

        # create stack config
        stack_name = self._stack_name
        deletion_protection_str = str(deletion_protection).lower()
        public_access_str = str(public_access).lower()
        config_content = f"""config:
  aws:region: {region}
  {project_name}:region: {region}
  {project_name}:pinecone-version: main-818794e
  {project_name}:vpc-cidr: {cidr}
  {project_name}:deletion-protection: {deletion_protection_str}
  {project_name}:public-access-enabled: {public_access_str}
  {project_name}:availability-zones:
"""
        for az in azs:
            config_content += f"    - {az}\n"

        # add custom AMI ID if provided
        if custom_ami_id:
            config_content += f"  {project_name}:custom-ami-id: {custom_ami_id}\n"

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
        import json

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
        import ipaddress

        try:
            target_net = ipaddress.ip_network(self.cidr)
        except ValueError:
            self._add_result(
                "VPC CIDR",
                False,
                f"Invalid CIDR: {self.cidr}",
                "Enter a valid CIDR block (e.g., 10.112.0.0/12)",
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
    HEADER_TITLE = "Pinecone BYOC Setup Wizard - GCP"
    HEADER_SUBTITLE = "This wizard will set up everything you need to deploy Pinecone BYOC on GCP."
    DEFAULT_CIDR = "10.112.0.0/12"
    DELETION_PROTECTION_DESC = "Protect AlloyDB databases and GCS buckets from accidental deletion"
    PRIVATE_ACCESS_DESC = "Private access requires Private Service Connect (more secure)"
    METADATA_NAME = "labels"
    CLOUD_NAME = "GCP"

    def run(self, output_dir: str = ".") -> bool:
        if self._headless:
            return self._run_headless(output_dir)

        self._print_header()

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
        zones = self._get_zones(region)
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
        )

    def _validate_gcp_creds(self) -> str | None:
        console.print()
        console.print(f"  {self._step('GCP Credentials')}")
        console.print()

        with Status("  [dim]Validating GCP credentials...[/]", console=console, spinner="dots"):
            try:
                try:
                    from google.auth import default

                    credentials, project_id = default()
                    if credentials and project_id:
                        console.print(
                            f"  [green]✓[/] GCP credentials valid [dim](Project: {project_id})[/]"
                        )
                        return project_id
                except ImportError:
                    pass

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
                    return project_id
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

    def _get_project_id(self, detected_project: str) -> str:
        console.print()
        console.print(f"  {self._step('GCP Project ID')}")
        console.print()
        return self._prompt("Enter GCP project ID", detected_project)

    def _get_region(self) -> str:
        console.print()
        console.print(f"  {self._step('GCP Region')}")
        console.print()
        return self._prompt("Enter GCP region", "us-central1")

    def _get_zones(self, region: str) -> list[str]:
        console.print()
        console.print(f"  {self._step('GCP Zones')}")
        console.print()

        default_zones = [f"{region}-a", f"{region}-b"]
        console.print(f"  [dim]Default zones for {region}:[/] {', '.join(default_zones)}")

        zones_input = self._prompt("Enter zones (comma-separated)", ",".join(default_zones))
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

cluster = PineconeGCPCluster(
    "pinecone-byoc",
    PineconeGCPClusterArgs(
        pinecone_api_key=config.require_secret("pinecone-api-key"),
        pinecone_version=config.require("pinecone-version"),
        project=gcp_config.require("project"),
        region=config.require("region"),
        availability_zones=config.require_object("availability-zones"),
        vpc_cidr=config.get("vpc-cidr") or "10.112.0.0/12",
        deletion_protection=config.get_bool("deletion-protection") if config.get_bool("deletion-protection") is not None else True,
        public_access_enabled=config.get_bool("public-access-enabled") if config.get_bool("public-access-enabled") is not None else True,
        labels=config.get_object("labels") or {},
    ),
)

update_kubeconfig_command = cluster.name.apply(
    lambda name: f"gcloud container clusters get-credentials {name} --region {config.require('region')} --project {gcp_config.require('project')}"
)
pulumi.export("environment", cluster.environment.env_name)
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
dependencies = ["pulumi-pinecone-byoc[gcp]"]
"""
        pyproject_path = os.path.join(output_dir, "pyproject.toml")
        with open(pyproject_path, "w") as f:
            f.write(pyproject_content)
        console.print("  [green]✓[/] Created pyproject.toml")

        # create stack config
        stack_name = self._stack_name
        deletion_protection_str = str(deletion_protection).lower()
        public_access_str = str(public_access).lower()
        config_content = f"""config:
  gcp:project: {project_id}
  {project_name}:region: {region}
  {project_name}:pinecone-version: main-818794e
  {project_name}:vpc-cidr: {cidr}
  {project_name}:deletion-protection: {deletion_protection_str}
  {project_name}:public-access-enabled: {public_access_str}
  {project_name}:availability-zones:
"""
        for zone in zones:
            config_content += f"    - {zone}\n"

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
    console.print()

    cloud = _read_input_with_placeholder("Enter choice (1 or 2)", "1")

    if cloud == "1":
        return "aws"
    elif cloud == "2":
        return "gcp"
    else:
        console.print(f"  [red]✗[/] Invalid choice: {cloud}")
        console.print("  [dim]Please choose 1 (AWS) or 2 (GCP)[/]")
        sys.exit(1)


def run_setup(
    output_dir: str = ".",
    cloud: str | None = None,
    headless: bool = False,
    stack_name: str = "prod",
    skip_install: bool = False,
) -> bool:
    try:
        if not cloud:
            if headless:
                console.print("  [red]✗[/] --cloud is required in headless mode")
                return False
            cloud = select_cloud()

        if cloud == "aws":
            wizard = AWSSetupWizard(
                headless=headless, stack_name=stack_name, skip_install=skip_install
            )
            return wizard.run(output_dir)
        elif cloud == "gcp":
            wizard = GCPSetupWizard(
                headless=headless, stack_name=stack_name, skip_install=skip_install
            )
            return wizard.run(output_dir)
        else:
            console.print(f"  [red]✗[/] Unknown cloud provider: {cloud}")
            console.print("  [dim]Valid options: aws, gcp[/]")
            return False

    except KeyboardInterrupt:
        console.print()
        console.print("  [yellow]Setup cancelled by user[/]")
        return False
    except Exception as e:
        console.print()
        console.print(f"  [red]✗[/] Setup failed: {e}")
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pinecone BYOC Setup Wizard")
    parser.add_argument("--output-dir", default=".", help="Directory to write project files")
    parser.add_argument(
        "--cloud",
        choices=["aws", "gcp"],
        help="Cloud provider (aws or gcp). If not specified, you will be prompted.",
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
    args = parser.parse_args()

    success = run_setup(
        args.output_dir,
        args.cloud,
        headless=args.headless,
        stack_name=args.stack_name,
        skip_install=args.skip_install,
    )
    sys.exit(0 if success else 1)
