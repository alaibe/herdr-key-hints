# Key hints for Herdr

A Herdr plugin (`herdr-plugin.toml`): the popup is `key_hints.py`, a single Python file that `run` starts, and the
macOS hold card is a Swift watcher (`watcher/`) that `watcher/setup.sh` builds and installs as a LaunchAgent.

## Checks

- CI runs these on every push, from the repository root, with Python 3.11 and ruff 0.17.5:

  ```bash
  ruff format --diff
  ruff check
  python3 -m unittest discover tests
  swift format lint --strict --recursive .
  ```

- On the shell scripts: `bash -n`, `shfmt -d -i 4 -ci -bn` and `shellcheck -S warning`.
- `ruff format` and `swift format format --in-place --recursive .` fix the formatting.

## Rules

- Python 3.11 is the floor: use nothing newer, and add no dependency beyond the standard library.
- A comment is one line, or there is none.
- The README stays one screen: what it is, installing it, the hold card and its settings.
