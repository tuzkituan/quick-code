#!/usr/bin/env python3
"""quick-code: tray menu listing the subdirectories of a folder, each openable
in VS Code, a terminal, or the file manager."""

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
    "root": "~/projects",
    "editor": ["code"],
    # null = auto-detect from TERMINALS below
    "terminal": None,
    "show_hidden": False,
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
    else:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    return config


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
    def __init__(self, config):
        self.root = Path(os.path.expanduser(config["root"])).resolve()
        self.editor = config["editor"]
        self.terminal = config["terminal"] or detect_terminal()
        self.show_hidden = config["show_hidden"]
        self.rebuild_pending = 0

        self.indicator = AppIndicator.Indicator.new(
            APP_ID, "folder-symbolic", AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Quick Code")

        self.monitor = None
        if self.root.is_dir():
            self.monitor = Gio.File.new_for_path(str(self.root)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None
            )
            self.monitor.connect("changed", self.on_root_changed)

        self.rebuild_menu()

    def list_projects(self):
        try:
            entries = [
                p for p in self.root.iterdir()
                if p.is_dir() and (self.show_hidden or not p.name.startswith("."))
            ]
        except OSError:
            return None
        return sorted(entries, key=lambda p: p.name.lower())

    def rebuild_menu(self):
        self.rebuild_pending = 0
        menu = Gtk.Menu()

        projects = self.list_projects()
        if projects is None:
            self.add_item(menu, f"Root not found: {self.root}", None)
        elif not projects:
            self.add_item(menu, f"No folders in {self.root}", None)
        else:
            for project in projects:
                item = Gtk.MenuItem(label=project.name, use_underline=False)
                item.set_submenu(self.project_menu(str(project)))
                menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())
        self.add_item(menu, "Open root in Files", lambda _: self.open_files(str(self.root)))
        self.add_item(menu, "Refresh", lambda _: self.rebuild_menu())
        self.add_item(menu, "Quit", lambda _: Gtk.main_quit())

        menu.show_all()
        self.indicator.set_menu(menu)
        return GLib.SOURCE_REMOVE

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
    parser.add_argument("--root", help="folder whose subdirectories are listed")
    args = parser.parse_args()

    config = load_config()
    if args.root:
        config["root"] = args.root

    # Launched apps are never waited on; let the kernel reap them.
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    for sig in (signal.SIGINT, signal.SIGTERM):
        unix_signal_add(GLib.PRIORITY_DEFAULT, sig, Gtk.main_quit)

    QuickCode(config)
    Gtk.main()


if __name__ == "__main__":
    main()
