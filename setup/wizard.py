"""
Pinecone BYOC Setup Wizard - Multi-Cloud Entry Point.

Interactive setup that creates a complete Pulumi project for BYOC deployment.
Supports AWS and GCP cloud providers.
"""

import sys
import platform
from rich.console import Console
from rich.panel import Panel

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
                    if not result and placeholder:
                        show_placeholder()
                        placeholder_visible = True
                continue

            # ctrl+c
            if char == "\x03":
                raise KeyboardInterrupt

            # normal character
            if placeholder_visible:
                clear_placeholder()
                placeholder_visible = False

            if password:
                sys.stdout.write("*")
            else:
                sys.stdout.write(char)
            sys.stdout.flush()
            result.append(char)

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        tty_file.close()
        sys.stdout.write("\n")
        sys.stdout.flush()

    return "".join(result)


def _read_input_with_placeholder_windows(
    prompt: str, placeholder: str = "", password: bool = False
) -> str:
    """Read input with a dimmed placeholder (Windows implementation)."""
    import msvcrt

    console.print(f"  {prompt}: ", end="")

    def show_placeholder():
        if placeholder and not password:
            sys.stdout.write(f"\033[2m{placeholder}\033[0m")
            sys.stdout.write(f"\033[{len(placeholder)}D")
            sys.stdout.flush()

    def clear_placeholder():
        if placeholder and not password:
            sys.stdout.write(" " * len(placeholder))
            sys.stdout.write(f"\033[{len(placeholder)}D")
            sys.stdout.flush()

    show_placeholder()
    result = []
    placeholder_visible = True

    while True:
        if msvcrt.kbhit():
            char = msvcrt.getch()

            # enter
            if char in (b"\r", b"\n"):
                if not result and placeholder:
                    result = list(placeholder.encode())
                break

            # tab - complete with placeholder
            if char == b"\t":
                if placeholder and not result:
                    clear_placeholder()
                    result = list(placeholder.encode())
                    sys.stdout.write(placeholder)
                    sys.stdout.flush()
                    placeholder_visible = False
                continue

            # backspace
            if char in (b"\x08", b"\x7f"):
                if result:
                    result.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                    if not result and placeholder:
                        show_placeholder()
                        placeholder_visible = True
                continue

            # ctrl+c
            if char == b"\x03":
                raise KeyboardInterrupt

            # normal character
            if placeholder_visible:
                clear_placeholder()
                placeholder_visible = False

            if password:
                sys.stdout.write("*")
            else:
                sys.stdout.write(char.decode("utf-8", errors="replace"))
            sys.stdout.flush()
            result.append(char)

    sys.stdout.write("\n")
    sys.stdout.flush()
    return b"".join(result).decode("utf-8", errors="replace")


def _read_input_with_placeholder(
    prompt: str, placeholder: str = "", password: bool = False
) -> str:
    """Read input with optional placeholder (platform-agnostic)."""
    if IS_WINDOWS:
        return _read_input_with_placeholder_windows(prompt, placeholder, password)
    else:
        return _read_input_with_placeholder_unix(prompt, placeholder, password)


def select_cloud() -> str:
    """Prompt user to select cloud provider."""
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


def run_setup(output_dir: str = ".", cloud: str = None) -> bool:
    """Run the interactive setup wizard."""
    try:
        # Select cloud if not specified
        if not cloud:
            cloud = select_cloud()

        # Dispatch to cloud-specific wizard
        if cloud == "aws":
            from .aws_wizard import run_setup as aws_run_setup

            return aws_run_setup(output_dir)
        elif cloud == "gcp":
            from .gcp_wizard import run_setup as gcp_run_setup

            return gcp_run_setup(output_dir)
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
    parser.add_argument(
        "--output-dir", default=".", help="Directory to write project files"
    )
    parser.add_argument(
        "--cloud",
        choices=["aws", "gcp"],
        help="Cloud provider (aws or gcp). If not specified, you will be prompted.",
    )
    args = parser.parse_args()

    success = run_setup(args.output_dir, args.cloud)
    sys.exit(0 if success else 1)
