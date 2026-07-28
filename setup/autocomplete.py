"""Raw-terminal input readers for the setup wizard.

Provides interactive prompts that work over a bare `/dev/tty` (so they behave
correctly even when the wizard is piped in via `curl ... | bash`):

- ``read_input_with_placeholder`` - a prompt with a dim default that Tab/→ completes.
- ``read_input_with_cycle`` - a prompt where Tab cycles through a list of options
  (with a trailing blank entry so wrapping returns to an empty "create new" value).

Both support in-place editing: ←/→ move the cursor, Home/End (or Ctrl+A/Ctrl+E)
jump to either end, Backspace and Delete remove either side of it, and typed
characters are inserted at the cursor.
"""

import platform
import sys

from rich.console import Console

IS_WINDOWS = platform.system() == "Windows"
if not IS_WINDOWS:
    import termios
    import tty
else:
    import msvcrt

console = Console()

RIGHT = "C"
LEFT = "D"
HOME = ("H", "1~", "7~")
END = ("F", "4~", "8~")
DELETE = "3~"
SHIFT_TAB = "Z"
NAVIGATION = (LEFT, RIGHT, DELETE) + HOME + END

_WINDOWS_SPECIAL = {b"K": LEFT, b"M": RIGHT, b"G": HOME[0], b"O": END[0], b"S": DELETE}


class _LineEditor:
    """A single-line buffer with a cursor, redrawn in place after every change.

    The prompt itself is already on screen, so redraws are relative: back up to
    the start of the value, rewrite it, pad over anything the old value left
    behind, then walk the cursor back to where it belongs.
    """

    def __init__(self, password: bool = False):
        self.chars: list[str] = []
        self.cursor = 0
        self.password = password
        self._shown_len = 0
        self._shown_cursor = 0

    @property
    def value(self) -> str:
        return "".join(self.chars)

    def set(self, value: str) -> None:
        self.chars = list(value)
        self.cursor = len(self.chars)
        self._redraw()

    def insert(self, char: str) -> None:
        self.chars.insert(self.cursor, char)
        self.cursor += 1
        self._redraw()

    def backspace(self) -> bool:
        if not self.cursor:
            return False
        del self.chars[self.cursor - 1]
        self.cursor -= 1
        self._redraw()
        return True

    def delete(self) -> bool:
        if self.cursor >= len(self.chars):
            return False
        del self.chars[self.cursor]
        self._redraw()
        return True

    def move_to(self, position: int) -> bool:
        position = max(0, min(position, len(self.chars)))
        if position == self.cursor:
            return False
        self.cursor = position
        self._redraw()
        return True

    def navigate(self, body: str) -> bool:
        if body == LEFT:
            return self.move_to(self.cursor - 1)
        if body == RIGHT:
            return self.move_to(self.cursor + 1)
        if body in HOME:
            return self.move_to(0)
        if body in END:
            return self.move_to(len(self.chars))
        if body == DELETE:
            return self.delete()
        return False

    def _redraw(self) -> None:
        display = "•" * len(self.chars) if self.password else self.value
        padding = max(0, self._shown_len - len(display))
        trailing = padding + len(display) - self.cursor
        sys.stdout.write(
            "\b" * self._shown_cursor + display + " " * padding + "\b" * max(0, trailing)
        )
        sys.stdout.flush()
        self._shown_len = len(display)
        self._shown_cursor = self.cursor


class _Prompt:
    """Key dispatch shared by both prompts; subclasses override what a key means."""

    def __init__(self, password: bool = False):
        self.editor = _LineEditor(password)

    def run(self, read_key) -> str:
        self.start()
        while True:
            char, body = read_key()

            if char in ("\r", "\n"):
                self.accept()
                return self.editor.value
            if char == "\t":
                self.tab(1)
            elif char == "\x1b":
                if body == SHIFT_TAB:
                    self.tab(-1)
                elif body in NAVIGATION:
                    self.navigate(body)
            elif char in ("\x7f", "\x08"):
                self.backspace()
            elif char == "\x01":
                self.jump(0)
            elif char == "\x05":
                self.jump(len(self.editor.chars))
            elif char == "\x03":
                raise KeyboardInterrupt
            elif char == "\x04":
                if not self.editor.chars:
                    raise EOFError
            elif char and ord(char) >= 32:
                self.insert(char)

    def start(self) -> None:
        pass

    def accept(self) -> None:
        pass

    def tab(self, step: int) -> None:
        pass

    def navigate(self, body: str) -> bool:
        return self.editor.navigate(body)

    def jump(self, position: int) -> bool:
        return self.editor.move_to(position)

    def backspace(self) -> bool:
        return self.editor.backspace()

    def insert(self, char: str) -> None:
        self.editor.insert(char)


class _PlaceholderPrompt(_Prompt):
    def __init__(self, placeholder: str, password: bool):
        super().__init__(password)
        self.placeholder = placeholder
        self.password = password
        self.visible = False

    def start(self) -> None:
        self._show()

    def accept(self) -> None:
        if not self.editor.chars and self.placeholder:
            self.editor.set(self.placeholder)

    def tab(self, step: int) -> None:
        if self._pristine():
            self._complete()

    def navigate(self, body: str) -> bool:
        # → on an untouched prompt completes the placeholder rather than moving
        if body == RIGHT and self._pristine():
            self._complete()
            return True
        return super().navigate(body)

    def backspace(self) -> bool:
        removed = super().backspace()
        if removed and not self.editor.chars:
            self._show()
        return removed

    def insert(self, char: str) -> None:
        if self.visible:
            self._erase()
        super().insert(char)

    def _pristine(self) -> bool:
        return bool(self.placeholder) and not self.editor.chars

    def _complete(self) -> None:
        self._erase()
        self.editor.set(self.placeholder)

    def _show(self) -> None:
        if self.placeholder and not self.password:
            sys.stdout.write(f"\033[2m{self.placeholder}\033[0m")  # dim
            sys.stdout.write(f"\033[{len(self.placeholder)}D")  # move back
            sys.stdout.flush()
            self.visible = True

    def _erase(self) -> None:
        if self.visible:
            sys.stdout.write(" " * len(self.placeholder))
            sys.stdout.write(f"\033[{len(self.placeholder)}D")
            sys.stdout.flush()
            self.visible = False


class _CyclePrompt(_Prompt):
    def __init__(self, options: list[str], default: str):
        super().__init__()
        # cycle sequence: default first (if any), then each option, then a blank
        # entry so wrapping returns to "create new"
        self.cycle = ([default] if default else []) + [o for o in options if o != default] + [""]
        self.default = default
        self.index = -1
        self.suggesting = False

    def start(self) -> None:
        if self.default:
            self.editor.set(self.default)
            self.index = 0
            self.suggesting = True

    def tab(self, step: int) -> None:
        self.suggesting = False
        self.index = (self.index + step) % len(self.cycle)
        self.editor.set(self.cycle[self.index])

    def navigate(self, body: str) -> bool:
        # reaching for the cursor means editing the suggestion, not replacing it --
        # even when the cursor was already at the end and cannot move
        self.suggesting = False
        moved = super().navigate(body)
        if moved and body == DELETE:
            self.index = -1
        return moved

    def jump(self, position: int) -> bool:
        self.suggesting = False
        return super().jump(position)

    def backspace(self) -> bool:
        self.suggesting = False
        removed = super().backspace()
        if removed:
            self.index = -1
        return removed

    def insert(self, char: str) -> None:
        # typing over a suggested default replaces it; otherwise resets the cycle
        if self.suggesting:
            self.editor.set("")
            self.suggesting = False
        super().insert(char)
        self.index = -1


def _read_key_unix(tty_file) -> tuple[str, str]:
    """Return (char, escape_body); escape_body is set only for escape sequences."""
    char = tty_file.read(1).decode("utf-8", errors="replace")
    if char != "\x1b":
        return char, ""

    intro = tty_file.read(1).decode("utf-8", errors="replace")
    if intro not in ("[", "O"):
        return "\x1b", ""

    body = ""
    while len(body) < 8:
        part = tty_file.read(1).decode("utf-8", errors="replace")
        body += part
        if part.isalpha() or part == "~":
            break
    return "\x1b", body


def _read_key_windows() -> tuple[str, str]:
    char_bytes = msvcrt.getch()
    if char_bytes in (b"\x00", b"\xe0"):
        return "\x1b", _WINDOWS_SPECIAL.get(msvcrt.getch(), "")
    try:
        return char_bytes.decode("utf-8", errors="replace"), ""
    except Exception:
        return "", ""


def _ask(prompt: str, session: _Prompt, fallback: str) -> str:
    if IS_WINDOWS and sys.platform != "win32":
        return fallback

    console.print(f"  {prompt}: ", end="")

    if IS_WINDOWS:
        try:
            return session.run(_read_key_windows)
        finally:
            console.print()

    # open /dev/tty directly to handle curl pipe case where stdin is not a TTY
    # use binary mode with no buffering to avoid input lag
    tty_file = open("/dev/tty", "rb", buffering=0)
    try:
        fd = tty_file.fileno()
        old_settings = termios.tcgetattr(fd)
    except Exception:
        tty_file.close()
        raise

    try:
        tty.setraw(fd)
        return session.run(lambda: _read_key_unix(tty_file))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        tty_file.close()
        console.print()


def read_input_with_placeholder(prompt: str, placeholder: str = "", password: bool = False) -> str:
    return _ask(prompt, _PlaceholderPrompt(placeholder, password), placeholder or "")


def read_input_with_cycle(prompt: str, options: list[str], default: str = "") -> str:
    """Prompt where Tab cycles through `options` (plus a trailing blank = create new).
    Typing or backspacing switches back to free-text editing. When `default` is
    given it is shown and pre-selected, and Enter without input returns it."""
    return _ask(prompt, _CyclePrompt(options, default), default)
