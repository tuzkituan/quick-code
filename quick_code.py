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
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

try:
    gi.require_version("GLibUnix", "2.0")
    from gi.repository import GLibUnix

    unix_signal_add = GLibUnix.signal_add
except (ValueError, ImportError):
    unix_signal_add = GLib.unix_signal_add

APP_ID = "quick-code"
CONFIG_PATH = Path(GLib.get_user_config_dir()) / APP_ID / "config.json"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
RECENT_PATH = STATE_DIR / APP_ID / "recent.json"
PINNED_PATH = STATE_DIR / APP_ID / "pinned.json"
# Repos remembered as recently opened; the menu shows the first RECENT_IN_MENU.
RECENT_KEEP = 50
RECENT_IN_MENU = 10

DEFAULT_CONFIG = {
    # Folders searched for repos; also editable from the tray menu.
    "roots": ["~/projects"],
    "editor": ["code"],
    # null = auto-detect from TERMINALS below
    "terminal": None,
    "show_hidden": False,
    # How many folder levels below root to search for repos (1 = direct children only).
    "max_depth": 3,
    # Up to this many repos are listed directly in the menu. With more, the menu shows
    # recently opened ones plus "Search repos…", since tray menus cannot scroll.
    "menu_limit": 20,
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


def load_paths(file):
    try:
        return [Path(p) for p in json.loads(file.read_text())]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def save_paths(file, paths):
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps([str(p) for p in paths], indent=2) + "\n")
    except OSError as e:
        print(f"{APP_ID}: cannot save {file}: {e}", file=sys.stderr)


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
        self.menu_limit = config["menu_limit"]
        self.recent = load_paths(RECENT_PATH)
        self.pinned = load_paths(PINNED_PATH)
        # (label, path) for every repo found by the last scan, sorted by label.
        self.repos = []
        self.search_window = None
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
        per_root = []
        for root in self.roots:
            repos = self.scan(root, self.max_depth, visited) if root.is_dir() else None
            per_root.append((root, repos))
        self.watch(visited)
        self.repos = self.label_repos([r for _, repos in per_root for r in repos or []])
        labels = {path: label for label, path in self.repos}

        # Pinned repos head the menu in both layouts, in the order they were pinned.
        pinned = [p for p in self.pinned if p in labels]
        for path in pinned:
            self.add_repo(menu, labels[path], path)

        if not self.roots:
            self.add_item(menu, "No folders yet: use Add folder…", None)
        elif len(self.repos) > self.menu_limit:
            if pinned:
                menu.append(Gtk.SeparatorMenuItem())
            self.add_item(menu, "Search repos…", lambda _: self.show_search())
            recent = [p for p in self.recent if p in labels and p not in pinned]
            if recent:
                menu.append(Gtk.SeparatorMenuItem())
                for path in recent[:RECENT_IN_MENU]:
                    self.add_repo(menu, labels[path], path)
            for root, repos in per_root:
                if repos is None:
                    self.add_item(menu, f"Not found: {root}", None)
        else:
            for i, (root, repos) in enumerate(per_root):
                if len(self.roots) > 1:
                    if i or pinned:
                        menu.append(Gtk.SeparatorMenuItem())
                    self.add_item(menu, display_path(root), None)
                if repos is None:
                    self.add_item(menu, f"Not found: {root}", None)
                elif not repos:
                    self.add_item(menu, f"No git repos in {display_path(root)}", None)
                elif pinned and len(self.roots) == 1:
                    menu.append(Gtk.SeparatorMenuItem())
                unpinned = [p for p in repos or [] if p not in pinned]
                for path in sorted(unpinned, key=lambda p: labels[p].lower()):
                    self.add_repo(menu, labels[path], path)

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

    @staticmethod
    def label_repos(repos):
        """Sorted (label, path) pairs; repos sharing a name get their parent folder."""
        names = [r.name for r in repos]
        labelled = [
            (f"{r.name} ({r.parent.name})" if names.count(r.name) > 1 else r.name, r)
            for r in repos
        ]
        return sorted(labelled, key=lambda lr: lr[0].lower())

    def add_repo(self, menu, label, path):
        # Flat, never nested under group folders: GNOME shows submenus inline, so
        # nesting made the menu change width as groups were opened.
        item = Gtk.MenuItem(label=label, use_underline=False)
        item.set_submenu(self.project_menu(path))
        menu.append(item)

    def project_menu(self, path):
        sub = Gtk.Menu()
        self.add_item(sub, "VS Code", lambda _: self.open_repo(path, self.open_editor))
        self.add_item(
            sub, "Terminal", lambda _: self.open_repo(path, self.open_terminal),
            sensitive=self.terminal is not None,
        )
        self.add_item(sub, "Files", lambda _: self.open_repo(path, self.open_files))
        sub.append(Gtk.SeparatorMenuItem())
        if path in self.pinned:
            self.add_item(sub, "Unpin", lambda _: self.toggle_pin(path))
        else:
            self.add_item(sub, "Pin to top", lambda _: self.toggle_pin(path))
        return sub

    def toggle_pin(self, path):
        if path in self.pinned:
            self.pinned.remove(path)
        else:
            self.pinned.append(path)
        save_paths(PINNED_PATH, self.pinned)
        self.rebuild_menu()

    def open_repo(self, path, opener):
        opener(str(path))
        self.recent = [path] + [p for p in self.recent if p != path]
        del self.recent[RECENT_KEEP:]
        save_paths(RECENT_PATH, self.recent)
        self.rebuild_menu()

    def show_search(self):
        if self.search_window is None:
            self.search_window = SearchWindow(self)
            self.search_window.connect("destroy", self.on_search_closed)
        self.search_window.present()

    def on_search_closed(self, _window):
        self.search_window = None

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


class SearchWindow(Gtk.Window):
    """Type-to-filter list of every repo. Enter opens the selected one in the editor."""

    def __init__(self, app):
        super().__init__(title="Quick Code")
        self.app = app
        self.set_default_size(520, 560)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_keep_above(True)
        self.connect("key-press-event", self.on_key)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        self.add(box)

        self.entry = Gtk.SearchEntry(placeholder_text="Search repos")
        self.entry.connect("search-changed", self.on_search_changed)
        self.entry.connect("activate", lambda _: self.open_selected(app.open_editor))
        box.pack_start(self.entry, False, False, 0)

        self.listbox = Gtk.ListBox(activate_on_single_click=True)
        self.listbox.set_filter_func(self.matches)
        self.listbox.connect(
            "row-activated", lambda _, row: self.open_row(row, app.open_editor)
        )
        # Pinned first, then recently opened, then the rest alphabetically.
        order = app.pinned + [p for p in app.recent if p not in app.pinned]
        rank = {p: i for i, p in enumerate(order)}
        for label, path in sorted(app.repos, key=lambda lp: rank.get(lp[1], len(rank))):
            self.listbox.add(self.make_row(label, path))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add(self.listbox)
        box.pack_start(scroller, True, True, 0)

        hint = Gtk.Label(xalign=0)
        hint.set_markup(
            "<small>Enter VS Code · Ctrl+T Terminal · Ctrl+O Files · Ctrl+P pin · Esc close"
            "</small>"
        )
        hint.get_style_context().add_class("dim-label")
        box.pack_start(hint, False, False, 0)

        self.show_all()
        self.select_first()

    def make_row(self, label, path):
        row = Gtk.ListBoxRow()
        row.label, row.path = label, path
        row.haystack = f"{label} {display_path(path)}".lower()
        lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin=6)
        name = Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        where = Gtk.Label(
            label=display_path(path.parent), xalign=0, ellipsize=Pango.EllipsizeMode.END
        )
        where.get_style_context().add_class("dim-label")
        lines.pack_start(name, False, False, 0)
        lines.pack_start(where, False, False, 0)
        pin = Gtk.Button(relief=Gtk.ReliefStyle.NONE, valign=Gtk.Align.CENTER)
        pin.connect("clicked", lambda _: self.toggle_pin(row))
        row.pin = pin
        self.show_pin(row)
        layout = Gtk.Box(spacing=6)
        layout.pack_start(lines, True, True, 0)
        layout.pack_start(pin, False, False, 0)
        row.add(layout)
        return row

    def show_pin(self, row):
        pinned = row.path in self.app.pinned
        row.pin.set_image(Gtk.Image.new_from_icon_name(
            "starred-symbolic" if pinned else "non-starred-symbolic", Gtk.IconSize.BUTTON
        ))
        row.pin.set_tooltip_text("Unpin" if pinned else "Pin to top of the menu")

    def toggle_pin(self, row):
        self.app.toggle_pin(row.path)
        self.show_pin(row)

    def matches(self, row):
        return all(word in row.haystack for word in self.entry.get_text().lower().split())

    def visible_rows(self):
        return [row for row in self.listbox.get_children() if self.matches(row)]

    def select_first(self):
        rows = self.visible_rows()
        self.listbox.select_row(rows[0] if rows else None)

    def on_search_changed(self, _entry):
        self.listbox.invalidate_filter()
        self.select_first()

    def move_selection(self, step):
        rows = self.visible_rows()
        if not rows:
            return
        current = self.listbox.get_selected_row()
        i = rows.index(current) + step if current in rows else 0
        row = rows[max(0, min(i, len(rows) - 1))]
        self.listbox.select_row(row)
        row.grab_focus()
        self.entry.grab_focus_without_selecting()

    def open_row(self, row, opener):
        if opener is self.app.open_terminal and self.app.terminal is None:
            return
        self.app.open_repo(row.path, opener)
        self.destroy()

    def open_selected(self, opener):
        row = self.listbox.get_selected_row()
        if row is not None and self.matches(row):
            self.open_row(row, opener)

    def on_key(self, _widget, event):
        key = Gdk.keyval_name(event.keyval)
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if key == "Escape":
            self.destroy()
        elif key in ("Down", "Up"):
            self.move_selection(1 if key == "Down" else -1)
        elif ctrl and key in ("t", "T"):
            self.open_selected(self.app.open_terminal)
        elif ctrl and key in ("o", "O"):
            self.open_selected(self.app.open_files)
        elif ctrl and key in ("p", "P"):
            row = self.listbox.get_selected_row()
            if row is not None and self.matches(row):
                self.toggle_pin(row)
        else:
            return False
        return True


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
