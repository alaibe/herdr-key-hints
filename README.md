# Key hints for Herdr

Every key [Herdr](https://herdr.dev) answers to, in one popup sized to fit it. On macOS, hold ⌘, ⌘⇧ or ⌃ for half a
second and a smaller card shows just the keys under that modifier; let go and it closes.

```
┌Keys────────────────────────────────────────────────┐
│                                                    │
│   ⌘B  every key       ⌘O  next tab                 │
│   ⌘D  diff picker     ⌘P  project browser          │
│   ⌘I  previous tab    ⌘R  quick actions            │
│   ⌘J  last pane       ⌘T  new tab                  │
│   ⌘K  navigator       ⌘U  next agent needing you   │
│   ⌘N  new space       ⌘W  close tab                │
│                                                    │
│   Ghostty                                          │
│   ⌘-  smaller         ⌘C  copy                     │
│   ⌘0  reset size      ⌘V  paste                    │
│   ⌘=  bigger          ⌘↩  fullscreen               │
│                                                    │
└────────────────────────────────────────────────────┘
```

It reads the keys fresh each time it opens, so a binding you add or move shows up without touching anything here:

- your `config.toml`: the `[keys]` overrides, and every `[[keys.command]]`, labelled with its `description`;
- `herdr --default-config`: the defaults those overrides leave alone, prefix bindings included;
- `ghostty +list-keybinds`, when Ghostty is installed: its copy, paste, font-size, fullscreen and palette keys.

Unlike [which-key](https://github.com/CowboyVang/herdr-which-key), which lists the keys after the prefix, this shows
direct chords too (`cmd+k`, `ctrl+h`), so it suits a setup that has moved most bindings off the prefix.

## Install

```sh
herdr plugin install alaibe/herdr-key-hints
```

Herdr 0.9.5 or newer, and Python 3.11 or newer for `tomllib`. Herdr starts plugin commands with its server's `PATH`,
which on macOS under launchd leaves out Homebrew, so `run` also looks in `/opt/homebrew/bin` and `/usr/local/bin`.
Set `KEY_HINTS_PYTHON` in the server's environment to name one outright.

Bind the sheet to a key in `config.toml`:

```toml
[[keys.command]]
key = "cmd+b"
type = "plugin_action"
command = "alaibe.key-hints.sheet"
description = "every key"
```

Any key closes it.

## Holding ⌘ or ⌃ (macOS)

```sh
herdr plugin action invoke hold-on --plugin alaibe.key-hints
```

This builds a small watcher with `swiftc` (Xcode's command line tools: `xcode-select --install`), installs it as the
LaunchAgent `alaibe.key-hints.watcher` and starts it. From then on, holding ⌘, ⌘⇧ or ⌃ for 0.45 s in a terminal
opens the card for that modifier; ⌃ also lists the prefix bindings, when the prefix is a ⌃ chord. A key pressed
during the hold, as in ⌘C, opens nothing.

A terminal program never sees a modifier pressed on its own, and Herdr plugins can't watch keys, so the hold is noticed
outside Herdr. The watcher polls the modifier state while a terminal is frontmost, and costs nothing otherwise. It
polls rather than using an event tap because a tap needs Accessibility or Input Monitoring; this needs no permission.
On a hold it invokes the plugin's `hold-cmd`, `hold-cmd-shift` or `hold-ctrl` action.

**The card takes the next key.** It is a Herdr popup, and a popup receives every key while it is open. Holding ⌘ and
pressing K closes the card without opening the navigator; press ⌘K again. Herdr has no popup that lets keys through to
the pane underneath, and no API a plugin can use to hand a key back.

The watcher binary lives in the plugin's state directory rather than its install directory, which moves with each
update. Every Herdr start rebuilds it if an update changed `watcher/main.swift`.

To turn it off:

```sh
herdr plugin action invoke hold-off --plugin alaibe.key-hints
```

## Settings

Optional, in `config.toml` in the plugin's config directory (`herdr plugin config-dir alaibe.key-hints`):

```toml
# Leave Ghostty's keys out.
ghostty = false

# Your own words for Herdr's actions, by their config.toml names.
[labels]
goto = "jump anywhere"

# The watcher: the terminals it listens in, by bundle id, and how long a hold lasts. Run hold-on again to apply.
[hold]
apps = ["com.mitchellh.ghostty"]
delay = 0.45
```

By default the watcher listens in Ghostty, kitty, WezTerm, iTerm2, Alacritty and Terminal.

## When it doesn't show

- `herdr plugin log --plugin alaibe.key-hints` has each action's output, `hold-on` included.
- `~/Library/Logs/herdr-key-hints.log` has everything the watcher noticed: each hold, and why it opened nothing.
- `KEY_HINTS_FOR=5 sh run open hold cmd`, run in a Herdr pane from a clone of this repository, opens the ⌘ card for
  five seconds without holding anything.
- `sh run print --hold ctrl --width 100` prints a card as text, without Herdr.

## Development

```sh
herdr plugin link .
python3 -m unittest discover tests
```

A linked plugin skips build steps, but `hold-on` builds the watcher itself either way. CI (`.forgejo/workflows/ci.yml`)
runs shfmt, shellcheck, ruff, the tests and `swift format lint`, as the commands there.

This repository lives on git.laibe.cc, which runs its checks. The GitHub copy is a mirror.

## Licence

MIT
