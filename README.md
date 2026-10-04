# quick-code

Tray icon that lists every folder in `~/projects` (configurable). Each entry has a submenu
to open it in **VS Code**, a **Terminal**, or **Files**. The list updates live as folders
are added or removed.

## Requirements

GTK 3 and AppIndicator bindings for Python (`python3-gobject`, `libappindicator-gtk3`).
On GNOME the *AppIndicator and KStatusNotifierItem Support* extension must be enabled.

## Install

```sh
./install.sh            # symlinks to ~/.local/bin/quick-code, adds app menu + autostart entry
quick-code &            # start now (or log out and back in)
./install.sh --uninstall
```

Or run directly without installing: `./quick_code.py [--root PATH]`.

## Config

`~/.config/quick-code/config.json` is created on first run:

```json
{
  "root": "~/projects",
  "editor": ["code"],
  "terminal": null,
  "show_hidden": false
}
```

- `editor` / `terminal` are argv lists. `{dir}` is replaced with the folder path; if absent,
  the path is appended. Example: `"terminal": ["kitty", "--directory", "{dir}"]`.
- `terminal: null` auto-detects ptyxis, gnome-terminal, konsole, kitty, alacritty or xterm.

Restart quick-code after editing the config.
