#!/usr/bin/env python3
"""quick-code: tray menu listing the git repositories under one or more folders,
each openable in VS Code, a terminal, or the file manager."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
except (ValueError, ImportError):
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3 as AppIndicator
from gi.repository import Gio, GLib, Gtk

try:
    gi.require_version("GLibUnix", "2.0")
    from gi.repository import GLibUnix

    unix_signal_add = GLibUnix.signal_add
except (ValueError, ImportError):
    unix_signal_add = GLib.unix_signal_add

APP_ID = "quick-code"
CONFIG_PATH = Path(GLib.get_user_config_dir()) / APP_ID / "config.json"

DEFAULT_CONFIG = {
    # Folders searched for repos; also editable from the tray menu.
    "roots": ["~/projects"],
    "editor": ["code"],
    # null = auto-detect from TERMINALS below
    "terminal": None,
    "show_hidden": False,
    # How many folder levels below root to search for repos (1 = direct children only).
    "max_depth": 3,
}

# First installed one wins when "terminal" is not configured.
TERMINALS = [
    ["ptyxis", "--new-window", "--working-directory", "{dir}"],
    ["gnome-terminal", "--working-directory={dir}"],
    ["konsole", "--workdir", "{dir}"],
    ["kitty", "--directory", "{dir}"],
    ["alacritty", "--working-directory", "{dir}"],
    ["xterm"],
]


def load_config():
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            config.update(json.loads(CONFIG_PATH.read_text()))
        except (OSError, json.JSONDecodeError) as e:
            print(f"{APP_ID}: ignoring bad config {CONFIG_PATH}: {e}", file=sys.stderr)
        # Configs written before multi-root support have a single "root".
        legacy = config.pop("root", None)
        if legacy and config["roots"] == DEFAULT_CONFIG["roots"]:
            config["roots"] = [legacy]
    else:
        save_config(config)
    return config


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")


def display_path(path):
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def detect_terminal():
    for cmd in TERMINALS:
        if shutil.which(cmd[0]):
            return cmd
    return None


def build_command(template, directory):
    """Substitute {dir} in the template; append the dir if no placeholder is used."""
    if any("{dir}" in arg for arg in template):
        return [arg.replace("{dir}", directory) for arg in template]
    return [*template, directory]


def launch(cmd, cwd):
    try:
        subprocess.Popen(
            cmd,
            cwd=cwd,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        print(f"{APP_ID}: failed to run {cmd}: {e}", file=sys.stderr)


class QuickCode:
    def __init__(self, config, persist=True):
        self.config = config
        # False when roots came from --root, so the menu never overwrites the saved ones.
        self.persist = persist
        self.roots = [Path(os.path.expanduser(r)).resolve() for r in config["roots"]]
        self.editor = config["editor"]
        self.terminal = config["terminal"] or detect_terminal()
        self.show_hidden = config["show_hidden"]
        self.max_depth = config["max_depth"]
        self.rebuild_pending = 0
        # Every plain folder the last scan walked through is watched, so repos
        # cloned into a group folder show up too.
        self.monitors = {}

        self.indicator = AppIndicator.Indicator.new(
            APP_ID, "folder-symbolic", AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Quick Code")

        self.rebuild_menu()

    def scan(self, directory, depth, visited):
        """Return every git repo under directory, at most depth levels down. Repos
        are not descended into, so nested node_modules etc. are never walked."""
        visited.add(directory)
        repos = []
        try:
            entries = list(directory.iterdir())
        except OSError:
            return repos
        for p in entries:
            if not p.is_dir() or (not self.show_hidden and p.name.startswith(".")):
                continue
            # .git is a file in worktrees and submodules, hence exists() not is_dir()
            if (p / ".git").exists():
                repos.append(p)
            elif depth > 1:
                repos.extend(self.scan(p, depth - 1, visited))
        return repos

    def watch(self, directories):
        for path in self.monitors.keys() - directories:
            self.monitors.pop(path).cancel()
        for path in directories - self.monitors.keys():
            monitor = Gio.File.new_for_path(str(path)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None
            )
            monitor.connect("changed", self.on_root_changed)
            self.monitors[path] = monitor

    def rebuild_menu(self):
        self.rebuild_pending = 0
        menu = Gtk.Menu()

        visited = set()
        if not self.roots:
            self.add_item(menu, "No folders yet: use Add folder…", None)
        for i, root in enumerate(self.roots):
            if len(self.roots) > 1:
                if i:
                    menu.append(Gtk.SeparatorMenuItem())
                self.add_item(menu, display_path(root), None)
            if not root.is_dir():
                self.add_item(menu, f"Not found: {root}", None)
                continue
            repos = self.scan(root, self.max_depth, visited)
            if repos:
                self.add_repos(menu, repos)
            else:
                self.add_item(menu, f"No git repos in {display_path(root)}", None)
        self.watch(visited)

        menu.append(Gtk.SeparatorMenuItem())
        if len(self.roots) == 1:
            root = str(self.roots[0])
            self.add_item(menu, "Open root in Files", lambda _: self.open_files(root))
        elif self.roots:
            menu.append(self.roots_submenu("Open root in Files", self.open_files))
        self.add_item(menu, "Add folder…", lambda _: self.add_root())
        if self.roots:
            menu.append(self.roots_submenu("Remove folder", self.remove_root))
        self.add_item(menu, "Refresh", lambda _: self.rebuild_menu())
        self.add_item(menu, "Quit", lambda _: Gtk.main_quit())

        menu.show_all()
        self.indicator.set_menu(menu)
        return GLib.SOURCE_REMOVE

    def roots_submenu(self, label, action):
        item = Gtk.MenuItem(label=label, use_underline=False)
        sub = Gtk.Menu()
        for root in self.roots:
            self.add_item(sub, display_path(root), lambda _, r=str(root): action(r))
        item.set_submenu(sub)
        return item

    def add_root(self):
        dialog = Gtk.FileChooserDialog(
            title="Add a projects folder", action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        dialog.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL, "_Add", Gtk.ResponseType.ACCEPT
        )
        dialog.set_select_multiple(True)
        dialog.set_current_folder(str(Path.home()))
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            for name in dialog.get_filenames():
                path = Path(name).resolve()
                if path not in self.roots:
                    self.roots.append(path)
            self.save_roots()
        dialog.destroy()
        self.rebuild_menu()

    def remove_root(self, root):
        self.roots.remove(Path(root))
        self.save_roots()
        self.rebuild_menu()

    def save_roots(self):
        if self.persist:
            self.config["roots"] = [display_path(r) for r in self.roots]
            save_config(self.config)

    def add_repos(self, menu, repos):
        # One flat list: GNOME shows submenus inline, so nesting repos under their
        # group folders made the menu change width as groups were opened.
        names = [r.name for r in repos]
        labelled = [
            (f"{r.name} ({r.parent.name})" if names.count(r.name) > 1 else r.name, r)
            for r in repos
        ]
        for label, repo in sorted(labelled, key=lambda lr: lr[0].lower()):
            item = Gtk.MenuItem(label=label, use_underline=False)
            item.set_submenu(self.project_menu(str(repo)))
            menu.append(item)

    def project_menu(self, directory):
        sub = Gtk.Menu()
        self.add_item(sub, "VS Code", lambda _: self.open_editor(directory))
        self.add_item(
            sub, "Terminal", lambda _: self.open_terminal(directory),
            sensitive=self.terminal is not None,
        )
        self.add_item(sub, "Files", lambda _: self.open_files(directory))
        return sub

    @staticmethod
    def add_item(menu, label, callback, sensitive=True):
        item = Gtk.MenuItem(label=label, use_underline=False)
        if callback is None:
            item.set_sensitive(False)
        else:
            item.set_sensitive(sensitive)
            item.connect("activate", callback)
        menu.append(item)

    def open_editor(self, directory):
        launch(build_command(self.editor, directory), directory)

    def open_terminal(self, directory):
        launch(build_command(self.terminal, directory), directory)

    def open_files(self, directory):
        launch(["gio", "open", directory], directory)

    def on_root_changed(self, _monitor, _file, _other, _event):
        # Debounce bursts (e.g. git clone creating a dir then renaming it).
        if self.rebuild_pending:
            GLib.source_remove(self.rebuild_pending)
        self.rebuild_pending = GLib.timeout_add(300, self.rebuild_menu)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", action="append",
        help="folder to search for repos (repeatable; overrides the configured roots)",
    )
    args = parser.parse_args()

    config = load_config()
    if args.root:
        config["roots"] = args.root

    # Launched apps are never waited on; let the kernel reap them.
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    for sig in (signal.SIGINT, signal.SIGTERM):
        unix_signal_add(GLib.PRIORITY_DEFAULT, sig, Gtk.main_quit)

    QuickCode(config, persist=not args.root)
    Gtk.main()


if __name__ == "__main__":
    main()
