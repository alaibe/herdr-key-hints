#!/usr/bin/env python3
"""The keys Herdr answers to, in a popup sized to fit them.

run sheet                           every binding, until a key is pressed (the `sheet` pane)
run hold                            the keys under $KEY_HINTS_HOLD, until it is let go (the `hold` pane)
run open sheet                      open the sheet (the `sheet` action)
run open hold cmd|cmd+shift|ctrl    open the card for one held modifier (the `hold-*` actions)
run print [--hold MOD] [--width N] [--height N]
                                    the sheet or a card without colour, laid out for that size
run json                            the bindings as JSON
run launch-agent LABEL BINARY       the macOS watcher's LaunchAgent, as a plist on stdout
"""

import json
import math
import os
import plistlib
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import termios
import time
import tomllib
import tty
import unicodedata
from dataclasses import dataclass
from pathlib import Path

PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID", "alaibe.key-hints")

GLYPHS = {"cmd": "⌘", "super": "⌘", "ctrl": "⌃", "control": "⌃", "alt": "⌥", "option": "⌥", "shift": "⇧"}
# One order, so `shift+cmd+k` and `cmd+shift+k` read alike.
MODIFIERS = "⌘⌃⌥⇧"
NAMED = {
    "minus": "-",
    "plus": "+",
    "equal": "=",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "backtick": "`",
    "ampersand": "&",
    "space": "␣",
    "tab": "⇥",
    "enter": "↩",
    "esc": "⎋",
    "escape": "⎋",
    "backspace": "⌫",
    "delete": "⌦",
    "left": "←",
    "right": "→",
    "up": "↑",
    "down": "↓",
}

# Where Herdr's action names would mislead or run long.
LABELS = {
    "help": "all Herdr keys",
    "goto": "session navigator",
    "workspace_picker": "space picker",
    "open_notification_target": "jump to notification",
    "switch_tab": "go to tab",
    "switch_workspace": "go to space",
    "focus_agent": "go to agent",
    "cycle_pane_next": "next pane",
    "cycle_pane_previous": "previous pane",
    "zoom": "zoom pane",
    "toggle_sidebar": "sidebar",
}
# Not pressed in normal use; remote_image_paste only exists under `herdr --remote`.
SKIP = re.compile(r"^(prefix|remote_image_paste|navigate_.*)$")

# Ghostty's tabs, splits and windows are left out: Herdr does that job.
GHOSTTY_ACTIONS = {
    "copy_to_clipboard": "copy",
    "paste_from_clipboard": "paste",
    "increase_font_size": "bigger",
    "decrease_font_size": "smaller",
    "reset_font_size": "reset size",
    "toggle_fullscreen": "fullscreen",
    "toggle_command_palette": "Ghostty palette",
}
GHOSTTY_APP = "/Applications/Ghostty.app/Contents/MacOS/ghostty"

DIRECTION = re.compile(r"^(.*?)\s*\b(left|down|up|right)$")

PREFIX = "prefix"
GHOSTTY = "Ghostty"
ORDER = ["⌘", "⌘⇧", "⌃", "⌃⇧", "⌥", "⌥⇧"]
HOLDS = {"cmd": "⌘", "cmd+shift": "⌘⇧", "ctrl": "⌃"}

# The watcher's default [hold] apps.
TERMINALS = [
    "com.mitchellh.ghostty",
    "net.kovidgoyal.kitty",
    "com.github.wez.wezterm",
    "com.googlecode.iterm2",
    "org.alacritty",
    "com.apple.Terminal",
]

# How long a modifier is held before its card opens: long enough that ⌘C or ⌃C never flashes it.
DELAY = 0.45

ROWS = 6
GAP = 4
KEY_GAP = 2
# Around the card: the popup's one-cell border and the space inside it.
CHROME_X = 2 + 2 * 3
CHROME_Y = 2 + 2 * 1

# Nothing is dimmed, so every line keeps the theme's full contrast.
KEY_STYLE = "\x1b[1;36m"
TITLE_STYLE = "\x1b[1m"
RESET = "\x1b[0m"


@dataclass(frozen=True)
class Key:
    section: str  # the modifiers of a direct chord, PREFIX or GHOSTTY
    mods: str  # the modifiers pressed with the key itself
    cap: str  # what to press: ⌘K, or V after the prefix
    label: str


@dataclass(frozen=True)
class Card:
    columns: int
    width: int
    lines: list[list[tuple[str, str]]]  # each line a run of (text, style)

    @property
    def height(self) -> int:
        return len(self.lines)


class HerdrError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def cells(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def keycap(chord: str) -> tuple[str, str]:
    """`cmd+shift+k` -> ("⌘⇧", "⌘⇧K"). `cmd++` is cmd and the plus key, not an empty key."""
    parts = chord[:-2].split("+") + ["+"] if chord.endswith("++") else chord.split("+")
    glyphs = [GLYPHS.get(part, part) for part in parts[:-1]]
    mods = "".join(sorted(glyphs, key=lambda g: MODIFIERS.index(g) if g in MODIFIERS else len(MODIFIERS)))
    last = parts[-1]
    last = NAMED.get(last, last if len(last) > 1 else last.upper())
    return mods, mods + last


def words(action: str) -> str:
    return LABELS.get(action) or action.replace("workspace", "space").replace("_", " ")


def read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except FileNotFoundError:
        return {}


def herdr_config() -> dict:
    return read_toml(Path(os.environ.get("HERDR_CONFIG_PATH") or Path.home() / ".config" / "herdr" / "config.toml"))


def plugin_config() -> dict:
    folder = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    return read_toml(Path(folder) / "config.toml") if folder else {}


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"


def herdr_defaults() -> dict[str, str]:
    """The commented `# action = "key"` lines of [keys] in `herdr --default-config`."""
    try:
        text = subprocess.run([herdr_bin(), "--default-config"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return {}
    section = text.split("\n[keys]\n", 1)[-1].split("\n[", 1)[0]
    defaults = {}
    for line in section.splitlines():
        found = re.match(r'#\s*([a-z_]+)\s*=\s*"([^"]+)"', line)
        if found and found[1] not in {"key", "type", "command", "description", "width", "height"}:
            defaults[found[1]] = found[2]
    return defaults


def ghostty_keys() -> list[tuple[str, str]]:
    """(chord, label) for GHOSTTY_ACTIONS, from `ghostty +list-keybinds`; nothing when Ghostty isn't installed."""
    binary = GHOSTTY_APP if os.path.exists(GHOSTTY_APP) else shutil.which("ghostty")
    if not binary:
        return []
    try:
        text = subprocess.run([binary, "+list-keybinds"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    found: dict[str, str] = {}
    for line in text.splitlines():
        # Lazy, so `super+==increase_font_size` is the key `super+=`; chords only, not the copy and paste keys.
        bind = re.match(r"keybind = ((?:super|ctrl|alt|shift)\+.+?)=([a-z_]+)", line)
        if bind and bind[2] in GHOSTTY_ACTIONS and bind[2] not in found:
            found[bind[2]] = bind[1]
    return [(found[action], GHOSTTY_ACTIONS[action]) for action in GHOSTTY_ACTIONS if action in found]


def sort_key(key: Key) -> tuple:
    if key.section in ORDER:
        section = ORDER.index(key.section)
    elif key.section == PREFIX:
        section = len(ORDER) + 1
    elif key.section == GHOSTTY:
        section = len(ORDER) + 2
    else:
        section = len(ORDER)
    base = key.cap.lstrip(MODIFIERS)
    return (section, key.section, base.lower(), key.mods)


def collect(
    keys: dict,
    defaults: dict[str, str],
    ghostty: list[tuple[str, str]],
    labels: dict[str, str] | None = None,
) -> list[Key]:
    """Every binding as a Key, in card order. `keys` is config.toml's [keys]; `labels` overrides action labels."""
    labels = labels or {}
    actions: dict[str, list[str]] = {name: [chord] for name, chord in defaults.items() if not SKIP.match(name)}
    for name, chord in keys.items():
        if SKIP.match(name) or name == "command":
            continue
        if isinstance(chord, str):
            actions[name] = [chord]
        elif isinstance(chord, list):
            actions[name] = [c for c in chord if isinstance(c, str)]
    rows = [(chord, labels.get(name) or words(name)) for name, chords in actions.items() for chord in chords if chord]
    for entry in keys.get("command", []):
        if isinstance(entry, dict) and entry.get("key"):
            rows.append((entry["key"], entry.get("description") or str(entry.get("command", ""))))

    out = []
    for chord, label in rows:
        if chord.startswith("prefix+"):
            mods, cap = keycap(chord.removeprefix("prefix+"))
            out.append(Key(PREFIX, mods, cap, label))
        else:
            mods, cap = keycap(chord)
            out.append(Key(mods, mods, cap, label))
    out += [Key(GHOSTTY, *keycap(chord), label) for chord, label in ghostty]
    return merge_directions(sorted(out, key=sort_key))


def merge_directions(keys: list[Key]) -> list[Key]:
    """`⌃H focus pane left` ... `⌃L focus pane right` -> one `⌃HJKL focus pane ←↓↑→`."""
    merged: list[Key] = []
    seen: dict[tuple[str, str, str], int] = {}
    for key in keys:
        found = DIRECTION.match(key.label)
        if not found:
            merged.append(key)
            continue
        group = (key.section, key.mods, found[1])
        letter = key.cap.removeprefix(key.mods)
        if group in seen:
            first = merged[seen[group]]
            merged[seen[group]] = Key(first.section, first.mods, first.cap + letter, first.label + NAMED[found[2]])
        else:
            seen[group] = len(merged)
            merged.append(Key(key.section, key.mods, key.cap, f"{found[1]} {NAMED[found[2]]}"))
    return merged


def bindings() -> list[Key]:
    config = plugin_config()
    labels = config.get("labels", {})
    labels = {name: text for name, text in labels.items() if isinstance(text, str)} if isinstance(labels, dict) else {}
    ghostty = ghostty_keys() if config.get("ghostty", True) else []
    return collect(herdr_config().get("keys", {}), herdr_defaults(), ghostty, labels)


def prefix_cap() -> str:
    return keycap(herdr_config().get("keys", {}).get("prefix", "ctrl+b"))


def sections(keys: list[Key], hold: str | None = None) -> list[tuple[str | None, list[Key]]]:
    """The card's sections with their titles; with `hold`, only what is pressed with that modifier."""
    prefix_mods, prefix = prefix_cap()
    if hold:
        held = HOLDS[hold]
        keys = [
            k
            for k in keys
            if k.section == held
            or (k.section == GHOSTTY and k.mods == held)
            or (k.section == PREFIX and prefix_mods == held)
        ]
    titles = {PREFIX: f"{prefix} then", GHOSTTY: "Ghostty"}
    grouped: dict[str, list[Key]] = {}
    for key in keys:
        grouped.setdefault(key.section, []).append(key)
    return [(titles.get(section), group) for section, group in grouped.items()]


def layout(groups: list[tuple[str | None, list[Key]]], columns: int, footer: str | None = None) -> Card:
    """Each section reads down, then across, in the same columns as every other, so keys line up the whole way."""
    heights = [math.ceil(len(keys) / columns) for _, keys in groups]
    key_width = [0] * columns
    label_width = [0] * columns
    for (_, keys), rows in zip(groups, heights, strict=True):
        for index, key in enumerate(keys):
            column = index // rows
            key_width[column] = max(key_width[column], cells(key.cap))
            label_width[column] = max(label_width[column], cells(key.label))
    used = [c for c in range(columns) if key_width[c]]
    width = sum(key_width[c] + KEY_GAP + label_width[c] for c in used) + GAP * max(len(used) - 1, 0)

    lines: list[list[tuple[str, str]]] = []
    for number, ((title, keys), rows) in enumerate(zip(groups, heights, strict=True)):
        if number:
            lines.append([])
        if title:
            lines.append([(title, TITLE_STYLE)])
            width = max(width, cells(title))
        for row in range(rows):
            runs: list[tuple[str, str]] = []
            for column in range(columns):
                index = column * rows + row
                if index >= len(keys):
                    break
                key = keys[index]
                # Keys right-aligned, so a short ⌘B sits against its label rather than across a gap sized for ⌃HJKL.
                runs.append((" " * (GAP if column else 0) + " " * (key_width[column] - cells(key.cap)), ""))
                runs.append((key.cap, KEY_STYLE))
                runs.append((" " * KEY_GAP + key.label + " " * (label_width[column] - cells(key.label)), ""))
            text, style = runs[-1]
            runs[-1] = (text.rstrip(), style)
            lines.append(runs)
    if footer:
        width = max(width, cells(footer))
        lines += [[], [(" " * (width - cells(footer)) + footer, "")]]
    return Card(columns, width, lines)


def fit(groups: list[tuple[str | None, list[Key]]], max_width: int, max_height: int, footer: str | None = None) -> Card:
    """The card with ROWS-deep sections, widened while it is too tall and narrowed while it is too wide."""
    biggest = max((len(keys) for _, keys in groups), default=1)
    columns = max(1, math.ceil(biggest / ROWS))
    while columns < biggest and layout(groups, columns, footer).height > max_height:
        if layout(groups, columns + 1, footer).width > max_width:
            break
        columns += 1
    while columns > 1 and layout(groups, columns, footer).width > max_width:
        columns -= 1
    return layout(groups, columns, footer)


def plain(line: list[tuple[str, str]]) -> str:
    return "".join(text for text, _ in line)


def styled(line: list[tuple[str, str]]) -> str:
    return "".join(f"{style}{text}{RESET}" if style else text for text, style in line)


def card_for(entry: str, hold: str | None, max_width: int, max_height: int) -> Card:
    groups = sections(bindings(), hold)
    if not any(keys for _, keys in groups):
        empty = "No keys bound under this modifier."
        return Card(1, cells(empty), [[(empty, "")]])
    footer = "any key closes" if entry == "sheet" else None
    columns = os.environ.get("KEY_HINTS_COLUMNS", "")
    if columns.isdigit() and int(columns) > 0:
        return layout(groups, int(columns), footer)
    return fit(groups, max_width, max_height, footer)


def draw(card: Card) -> None:
    """The card centred in the popup, with the cursor hidden and wrapping off until restore()."""
    size = shutil.get_terminal_size()
    top = max((size.lines - card.height) // 2, 0)
    # A popup's terminal is one column narrower than the space inside its border; centre in what shows.
    left = max((size.columns + 1 - card.width) // 2, 0)
    out = ["\x1b[?25l\x1b[?7l\x1b[2J"]
    for number, line in enumerate(card.lines[: size.lines]):
        out.append(f"\x1b[{top + number + 1};{left + 1}H{styled(line)}")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def show(card: Card) -> None:
    """Draw the card, and again whenever the popup's size changes: Herdr can still be sizing it when this starts."""
    signal.signal(signal.SIGWINCH, lambda *_: draw(card))
    draw(card)


def restore() -> None:
    sys.stdout.write("\x1b[?7h\x1b[?25h")
    sys.stdout.flush()


def inner_size() -> tuple[int, int]:
    size = shutil.get_terminal_size()
    return size.columns - 4, size.lines - 2


def sheet() -> None:
    show(card_for("sheet", None, *inner_size()))
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        os.read(fd, 64)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        restore()


class Modifiers:
    """CoreGraphics' modifier state and time since the last key press, readable without any permission."""

    COMBINED_SESSION, KEY_DOWN = 0, 10
    SHIFT, CTRL, ALT, CMD = 1 << 17, 1 << 18, 1 << 19, 1 << 20

    def __init__(self) -> None:
        import ctypes

        self.cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        self.cg.CGEventSourceFlagsState.restype = ctypes.c_uint64
        self.cg.CGEventSourceFlagsState.argtypes = [ctypes.c_int32]
        self.cg.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double
        self.cg.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_int32, ctypes.c_uint32]

    def held(self) -> str | None:
        """The modifier set held now, named as in HOLDS, or None."""
        flags = self.cg.CGEventSourceFlagsState(self.COMBINED_SESSION)
        cmd, ctrl, shift = bool(flags & self.CMD), bool(flags & self.CTRL), bool(flags & self.SHIFT)
        if flags & self.ALT:
            return None
        if cmd and not ctrl:
            return "cmd+shift" if shift else "cmd"
        if ctrl and not cmd and not shift:
            return "ctrl"
        return None

    def since_key(self) -> float:
        return self.cg.CGEventSourceSecondsSinceLastEventType(self.COMBINED_SESSION, self.KEY_DOWN)


def hold() -> None:
    """The card for $KEY_HINTS_HOLD until it is let go or a key comes; KEY_HINTS_FOR=<seconds> keeps it that long."""
    which = os.environ.get("KEY_HINTS_HOLD", "cmd")
    which = which if which in HOLDS else "cmd"
    keep = float(os.environ.get("KEY_HINTS_FOR") or 0)
    modifiers = Modifiers() if sys.platform == "darwin" and not keep else None
    # A key pressed while this started has already reached Herdr (⌘T opened its tab): the card would be in the way.
    if modifiers and (modifiers.held() != which or modifiers.since_key() < hold_delay()):
        return
    show(card_for("hold", which, *inner_size()))

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    start = time.monotonic()
    try:
        tty.setcbreak(fd)
        while True:
            elapsed = time.monotonic() - start
            if keep and elapsed > keep:
                break
            if modifiers and (modifiers.held() != which or modifiers.since_key() < elapsed):
                break
            # A key that reached the popup closes it too: Herdr gives a popup every key while it is open.
            if select.select([fd], [], [], 0.05)[0] or elapsed > 30:
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        restore()


def hold_delay() -> float:
    config = plugin_config().get("hold", {})
    delay = config.get("delay", DELAY) if isinstance(config, dict) else DELAY
    return float(delay) if type(delay) in (int, float) and delay > 0 else DELAY


def request(method: str, params: dict) -> dict:
    """One request over Herdr's socket API. The CLI's `plugin pane open` can't size a popup; the socket can."""
    path = os.environ.get("HERDR_SOCKET_PATH") or str(Path.home() / ".config" / "herdr" / "herdr.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(path)
        sock.sendall(json.dumps({"id": "key-hints", "method": method, "params": params}).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    reply = json.loads(data or b"{}")
    if "error" in reply or "result" not in reply:
        error = reply.get("error") or {}
        message = error.get("message", data[:200].decode(errors="replace").strip() or "empty reply")
        raise HerdrError(str(error.get("code", "no_reply")), str(message))
    return reply["result"]


def terminal_area() -> tuple[int, int]:
    """The focused tab's size in cells, which bounds the popup."""
    snapshot = request("session.snapshot", {}).get("snapshot", {})
    for tab in snapshot.get("layouts", []):
        if tab.get("tab_id") == snapshot.get("focused_tab_id"):
            return tab["area"]["width"], tab["area"]["height"]
    return 80, 24


def open_popup(entry: str, which: str | None = None) -> None:
    try:
        width, height = terminal_area()
    except (OSError, HerdrError):
        width, height = 80, 24
    # A popup takes the next key, so none opens with nothing to show or the hold already over.
    if which and not any(keys for _, keys in sections(bindings(), which)):
        print(f"no {which} card: nothing is bound under it")
        return
    if which and sys.platform == "darwin" and not os.environ.get("KEY_HINTS_FOR"):
        modifiers = Modifiers()
        if modifiers.held() != which:
            print(f"no {which} card: the hold ended while it started")
            return
        if modifiers.since_key() < hold_delay():
            print(f"no {which} card: a key was pressed while it started")
            return
    card = card_for(entry, which, width - CHROME_X - 4, height - CHROME_Y - 2)
    env = {"KEY_HINTS_COLUMNS": str(card.columns)}
    if which:
        env["KEY_HINTS_HOLD"] = which
    if os.environ.get("KEY_HINTS_FOR"):
        env["KEY_HINTS_FOR"] = os.environ["KEY_HINTS_FOR"]
    params = {
        "plugin_id": PLUGIN_ID,
        "entrypoint": entry,
        "placement": "popup",
        "width": min(card.width + CHROME_X, width),
        "height": min(card.height + CHROME_Y, height),
        "env": env,
        "focus": entry == "sheet",
    }
    try:
        request("plugin.pane.open", params)
    except HerdrError as error:
        # Settings, copy mode or another popup is up: a hold doesn't fight it.
        if which and error.code == "ui_busy":
            print(f"no {which} card: another popup or Herdr's settings are open")
            return
        raise


def launch_agent(label: str, binary: str) -> None:
    config = plugin_config().get("hold", {})
    if not isinstance(config, dict):
        sys.exit("key-hints: [hold] must be a table, with apps and delay under it")
    apps = config.get("apps", TERMINALS)
    delay = config.get("delay", DELAY)
    if not (isinstance(apps, list) and all(isinstance(app, str) for app in apps)):
        sys.exit('key-hints: [hold] apps must be a list of bundle ids, such as ["com.mitchellh.ghostty"]')
    if type(delay) not in (int, float) or delay <= 0:
        sys.exit("key-hints: [hold] delay must be a number of seconds, such as 0.45")
    log = str(Path.home() / "Library" / "Logs" / "herdr-key-hints.log")
    arguments = [binary, "--herdr", herdr_bin(), "--plugin", PLUGIN_ID, "--apps", ",".join(apps)]
    arguments += ["--delay", str(delay), "--log", log]
    agent = {
        "Label": label,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "StandardErrorPath": log,
    }
    sys.stdout.buffer.write(plistlib.dumps(agent))


def option(args: list[str], name: str, default: str | None = None) -> str | None:
    if name in args and args.index(name) + 1 < len(args):
        return args[args.index(name) + 1]
    return default


def main() -> None:
    args = sys.argv[1:]
    command = args[0] if args else ""
    if command == "sheet":
        sheet()
    elif command == "hold":
        hold()
    elif command == "open" and (args[1:2] == ["sheet"] or (args[1:2] == ["hold"] and args[2:3] and args[2] in HOLDS)):
        try:
            open_popup(args[1], args[2] if args[1] == "hold" else None)
        except (OSError, HerdrError) as error:
            sys.exit(f"key-hints: Herdr didn't open the {args[1]}: {error}")
    elif command == "print":
        which = option(args, "--hold")
        if which is not None and which not in HOLDS:
            sys.exit(f"key-hints: --hold takes {', '.join(HOLDS)}")
        width, height = int(option(args, "--width", "120") or 120), int(option(args, "--height", "40") or 40)
        card = card_for("hold" if which else "sheet", which, width, height)
        print("\n".join(plain(line).rstrip() for line in card.lines))
    elif command == "json":
        keys = [{"section": k.section, "mods": k.mods, "cap": k.cap, "label": k.label} for k in bindings()]
        print(json.dumps(keys, ensure_ascii=False))
    elif command == "launch-agent" and len(args) == 3:
        launch_agent(args[1], args[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
