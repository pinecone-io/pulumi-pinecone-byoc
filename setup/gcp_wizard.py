"""
Pinecone BYOC Setup Wizard - GCP Implementation.

Interactive setup that creates a complete Pulumi project for BYOC deployment on GCP.
"""

import os
import sys
import platform
from dataclasses import dataclass
from typing import Optional

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.status import Status

# Platform-specific imports
IS_WINDOWS = platform.system() == "Windows"
if not IS_WINDOWS:
    import tty
    import termios


# pinecone blue
BLUE = "#002BFF"

console = Console()


def _read_input_with_placeholder_unix(
    prompt: str, placeholder: str = "", password: bool = False
) -> str:
    """Read input with a dimmed placeholder (Unix implementation)."""
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


def _read_input_with_placeholder_windows(
    prompt: str, placeholder: str = "", password: bool = False
) -> str:
    """Read input with a dimmed placeholder (Windows implementation)."""
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
                    if char_bytes == b"\xe0" and special == b"M":  # right arrow
                        if placeholder and not result:
                            clear_placeholder()
                            result = list(placeholder)
                            sys.stdout.write(placeholder)
                            sys.stdout.flush()
                            placeholder_visible = False
                    continue

                try:
                    char = char_bytes.decode("utf-8", errors="replace")
                except:
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


def _read_input_with_placeholder(
    prompt: str, placeholder: str = "", password: bool = False
) -> str:
    """Read input with a dimmed placeholder that disappears when typing."""
    if IS_WINDOWS:
        return _read_input_with_placeholder_windows(prompt, placeholder, password)
    else:
        return _read_input_with_placeholder_unix(prompt, placeholder, password)


@dataclass
class PreflightResult:
    """Result of a single preflight check."""

    name: str
    passed: bool
    message: str
    details: Optional[str] = None


class GCPSetupWizard:
    """Interactive setup wizard for Pinecone BYOC deployment on GCP."""

    TOTAL_STEPS = 13

    def __init__(self):
        self.results: list[PreflightResult] = []
        self._current_step = 0

    def _step(self, title: str) -> str:
        """Return formatted step header and increment counter."""
        self._current_step += 1
        return f"[{BLUE}]Step {self._current_step}/{self.TOTAL_STEPS}[/] · {title}"

    def run(self, output_dir: str = ".") -> bool:
        """Run the interactive setup wizard."""
        self._print_header()

        # get pinecone api key
        api_key = self._get_api_key()
        if not api_key:
            return False

        # validate api key
        if not self._validate_api_key(api_key):
            return False

        # validate gcp credentials (early - we need them for everything else)
        project_id = self._validate_gcp_creds()
        if not project_id:
            return False

        # get project id (allow override of detected one)
        project_id = self._get_project_id(project_id)

        # get region
        region = self._get_region()

        # get availability zones
        zones = self._get_zones(region)

        # get vpc cidr
        cidr = self._get_cidr()

        # get deletion protection preference
        deletion_protection = self._get_deletion_protection()

        # get public access preference
        public_access = self._get_public_access()

        # get custom labels
        labels = self._get_labels()

        # run preflight checks
        if not self._run_preflight_checks(project_id, region, zones, cidr):
            return False

        # get project name
        project_name = self._get_project_name()

        # set up pulumi backend
        if not self._setup_pulumi_backend():
            return False

        # generate everything
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

    def _print_header(self):
        console.print()
        console.print(
            Panel.fit(
                f"[bold {BLUE}]Pinecone BYOC Setup Wizard - GCP[/]",
                border_style=BLUE,
                padding=(0, 2),
            )
        )
        console.print()
        console.print(
            "  This wizard will set up everything you need to deploy Pinecone BYOC on GCP.",
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
        console.print(f"  {self._step('Pinecone API Key')}")
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

    def _validate_gcp_creds(self) -> Optional[str]:
        """Validate GCP credentials early - we need them for everything else."""
        console.print()
        console.print(f"  {self._step('GCP Credentials')}")
        console.print()

        with Status(
            "  [dim]Validating GCP credentials...[/]", console=console, spinner="dots"
        ):
            try:
                # Try to use google.auth to get default credentials
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

                # Fall back to gcloud CLI
                import subprocess

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
                console.print(
                    "  [dim]Make sure you have valid GCP credentials configured.[/]"
                )
                console.print("  [dim]You can set them via:[/]")
                console.print("    [dim]· gcloud auth application-default login[/]")
                console.print(
                    "    [dim]· GOOGLE_APPLICATION_CREDENTIALS environment variable[/]"
                )
                console.print("    [dim]· gcloud config set project PROJECT_ID[/]")
                return None

    def _get_project_id(self, detected_project: str) -> str:
        """Get GCP project ID from user."""
        console.print()
        console.print(f"  {self._step('GCP Project ID')}")
        console.print()
        return self._prompt("Enter GCP project ID", detected_project)

    def _get_region(self) -> str:
        """Get GCP region from user."""
        console.print()
        console.print(f"  {self._step('GCP Region')}")
        console.print()
        return self._prompt("Enter GCP region", "us-central1")

    def _get_zones(self, region: str) -> list[str]:
        """Get availability zones from user."""
        console.print()
        console.print(f"  {self._step('GCP Zones')}")
        console.print()

        # Default zones for the region (2 zones like AWS)
        default_zones = [f"{region}-a", f"{region}-b"]
        console.print(
            f"  [dim]Default zones for {region}:[/] {', '.join(default_zones)}"
        )

        zones_input = self._prompt(
            "Enter zones (comma-separated)", ",".join(default_zones)
        )
        zones = [zone.strip() for zone in zones_input.split(",")]
        return zones

    def _get_cidr(self) -> str:
        """Get VPC CIDR from user."""
        console.print()
        console.print(f"  {self._step('VPC CIDR Block')}")
        console.print(
            "  [dim]The IP range for your VPC (must not conflict with existing VPCs)[/]"
        )
        console.print()
        return self._prompt("Enter CIDR block", "10.112.0.0/12")

    def _get_deletion_protection(self) -> bool:
        """Get deletion protection preference from user."""
        console.print()
        console.print(f"  {self._step('Deletion Protection')}")
        console.print(
            "  [dim]Protect AlloyDB databases and GCS buckets from accidental deletion[/]"
        )
        console.print()
        response = self._prompt("Enable deletion protection? (Y/n)", "Y")
        return response.lower() in ("y", "yes", "")

    def _get_public_access(self) -> bool:
        """Get public access preference from user."""
        console.print()
        console.print(f"  {self._step('Network Access')}")
        console.print("  [dim]Public access allows connections from the internet[/]")
        console.print(
            "  [dim]Private access requires Private Service Connect (more secure)[/]"
        )
        console.print()
        response = self._prompt("Enable public access? (Y/n)", "Y")
        return response.lower() in ("y", "yes", "")

    def _get_labels(self) -> dict[str, str]:
        """Get custom labels from user."""
        console.print()
        console.print(f"  {self._step('Resource Labels')}")
        console.print(
            "  [dim]Add custom labels to all GCP resources (for cost tracking, etc.)[/]"
        )
        console.print(
            "  [dim]Format: key=value, comma-separated (e.g., team=platform,env=prod)[/]"
        )
        console.print()

        labels_input = self._prompt("Enter labels (or press Enter to skip)", "")
        if not labels_input:
            return {}

        labels = {}
        for pair in labels_input.split(","):
            pair = pair.strip()
            if "=" in pair:
                key, value = pair.split("=", 1)
                labels[key.strip()] = value.strip()

        if labels:
            console.print(f"  [dim]Labels to apply: {labels}[/]")

        return labels

    def _run_preflight_checks(
        self, project_id: str, region: str, zones: list[str], cidr: str
    ) -> bool:
        """Run preflight checks for GCP environment."""
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

    def _get_project_name(self) -> str:
        """Get project name from user."""
        console.print()
        console.print(f"  {self._step('Project Name')}")
        console.print(
            "  [dim]A short name for this deployment (e.g., 'pinecone-prod')[/]"
        )
        console.print()
        return self._prompt("Enter project name", "pinecone-byoc")

    def _setup_pulumi_backend(self) -> bool:
        """Set up Pulumi backend (local or cloud)."""
        import subprocess

        console.print()
        console.print(f"  {self._step('Pulumi Backend')}")
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
        project_id: str,
        region: str,
        zones: list[str],
        cidr: str,
        deletion_protection: bool,
        public_access: bool,
        labels: dict[str, str],
    ):
        """Generate complete Pulumi project."""
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
            "description": "Pinecone BYOC deployment on GCP",
        }

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
            console.print(
                f"  [red]✗[/] Failed to install dependencies: {result.stderr.strip()}"
            )
            console.print("  [dim]Run manually:[/] uv sync")
            return False

        # create stack config
        stack_name = "prod"
        deletion_protection_str = str(deletion_protection).lower()
        public_access_str = str(public_access).lower()
        config_content = f"""config:
  gcp:project: {project_id}
  {project_name}:region: {region}
  {project_name}:pinecone-version: main-434e1c9
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
            console.print(
                f"  [red]✗[/] Failed to store API key: {result.stderr.strip()}"
            )
            console.print(
                "  [dim]Run manually:[/] pulumi config set --secret pinecone-api-key <key>"
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


class GCPPreflightChecker:
    """Runs preflight checks for GCP environment."""

    def __init__(self, project_id: str, region: str, zones: list[str], cidr: str):
        self.project_id = project_id
        self.region = region
        self.zones = zones
        self.cidr = cidr
        self.results: list[PreflightResult] = []

    def run_checks(self) -> bool:
        """Run all preflight checks."""
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

    def _gcloud_json(self, args: list[str]):
        """Run gcloud command and return parsed JSON. Raises RuntimeError on failure."""
        import subprocess

        result = subprocess.run(
            ["gcloud"] + args + [f"--project={self.project_id}", "--format=json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip().split("\n")[0])
        import json

        return json.loads(result.stdout)

    def _check_apis_enabled(self):
        """Check if required GCP APIs are enabled."""
        import subprocess

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
        """Check VPC network quota."""
        try:
            networks = self._gcloud_json(["compute", "networks", "list"])
            current = len(networks) if isinstance(networks, list) else 0
            # Default VPC quota is 15
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
        """Check external/static IP quota."""
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
                "Request quota increase for 'Static IP addresses'"
                if available < needed
                else None,
            )
        except Exception as e:
            self._add_result("External IPs", False, f"Failed to check: {e}")

    def _check_gke_quota(self):
        """Check GKE cluster count against quota."""
        try:
            data = self._gcloud_json(["container", "clusters", "list"])
            current = len(data) if isinstance(data, list) else 0
            # Default GKE quota is 50 clusters per zone per project
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
        """Check if required machine types are available in selected zones."""
        import subprocess

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
        """Check if zones are valid for the region."""
        import subprocess

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
                self._add_result(
                    "Availability Zones", True, "All requested zones available"
                )
        except Exception as e:
            self._add_result("Availability Zones", False, f"Failed to check: {e}")

    def _check_cidr_conflicts(self):
        """Check if CIDR is valid and doesn't conflict with existing VPCs."""
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
            for net in networks:
                for sub in net.get("subnetworks", []):
                    # subnetwork URLs contain the CIDR in a separate call, skip deep check
                    pass
            # Also check subnets directly in the region
            import subprocess

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


def run_setup(output_dir: str = ".") -> bool:
    """Run the interactive setup wizard."""
    wizard = GCPSetupWizard()
    return wizard.run(output_dir)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pinecone BYOC Setup Wizard - GCP")
    parser.add_argument(
        "--output-dir", default=".", help="Directory to write project files"
    )
    args = parser.parse_args()

    success = run_setup(args.output_dir)
    sys.exit(0 if success else 1)
