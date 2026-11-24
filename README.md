# Key hints for Herdr

Every key [Herdr](https://herdr.dev) answers to, in one popup sized to fit it: your `config.toml` bindings, Herdr's
defaults and Ghostty's keys. On macOS, hold ⌘, ⌘⇧ or ⌃ for half a second and a smaller card shows just the keys under
that modifier.

## Install

```sh
herdr plugin install alaibe/herdr-key-hints
```

Needs Herdr 0.9.5 and Python 3.11 or newer; set `KEY_HINTS_PYTHON` in Herdr's environment to name one. Bind the sheet
in `config.toml`:

```toml
[[keys.command]]
key = "cmd+b"
type = "plugin_action"
command = "alaibe.key-hints.sheet"
description = "every key"
```

## Usage

On macOS, turn the hold card on (it builds a watcher with `swiftc` and installs a LaunchAgent), and off before
uninstalling:

```sh
herdr plugin action invoke hold-on --plugin alaibe.key-hints
herdr plugin action invoke hold-off --plugin alaibe.key-hints
```

Settings go in `config.toml` in `herdr plugin config-dir alaibe.key-hints`; run `hold-on` again after changing `[hold]`:

```toml
ghostty = false
[labels]
goto = "jump anywhere"
[hold]
apps = ["com.mitchellh.ghostty"]
delay = 0.45
```

## Development

Python 3.11 and ruff 0.17.5; on macOS, Swift for the watcher. `python3 -m unittest discover tests` runs the tests, and
`AGENTS.md` lists the checks CI runs.

## License

[MIT](LICENSE).
