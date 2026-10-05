# quick-code

Tray icon that lists every git repo under one or more folders (`~/projects` by default).
Repos in nested folders (e.g. `~/projects/group/repo`) are included in one flat, sorted list;
folders that are not repos are never shown. Each repo has a submenu to open it in
**VS Code**, a **Terminal**, or **Files**. The list updates live as repos are added or removed.

Pick the folders from the tray with **Add folder…** and **Remove folder**, or list them in the
config.

## Requirements

GTK 3 and AppIndicator bindings for Python (`python3-gobject`, `libappindicator-gtk3`).
On GNOME the *AppIndicator and KStatusNotifierItem Support* extension must be enabled.

## Install

One-liner, no clone needed:

```sh
curl -fsSL https://raw.githubusercontent.com/tuzkituan/quick-code/main/install.sh | bash
```

The installer starts quick-code right away and adds it to autostart, so it comes back at
every login. Re-running it updates and restarts the running copy.

Uninstall: `curl -fsSL https://raw.githubusercontent.com/tuzkituan/quick-code/main/install.sh | bash -s -- --uninstall`

From a clone:

```sh
./install.sh            # symlinks to ~/.local/bin/quick-code, adds app menu + autostart, starts it
./install.sh --uninstall
```

Or run directly without installing: `./quick_code.py [--root PATH]...` (`--root` is repeatable
and overrides the configured folders for that run).

## Config

`~/.config/quick-code/config.json` is created on first run:

```json
{
  "roots": ["~/projects"],
  "editor": ["code"],
  "terminal": null,
  "show_hidden": false,
  "max_depth": 3
}
```

- `editor` / `terminal` are argv lists. `{dir}` is replaced with the folder path; if absent,
  the path is appended. Example: `"terminal": ["kitty", "--directory", "{dir}"]`.
- `roots` are the folders searched for repos. An older single `"root"` entry is still read.
- `max_depth` is how many folder levels below each root are searched for repos.
- `terminal: null` auto-detects ptyxis, gnome-terminal, konsole, kitty, alacritty or xterm.

Restart quick-code after editing the config by hand (tray changes apply immediately).

## License

MIT — see [LICENSE](LICENSE).
