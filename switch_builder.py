#!/usr/bin/env python3
"""SwitchWakerHD: a small window around make_sd_cached.py, for building (and updating) the Switch version.

Pick the game once (a .wua, .wux/.wud or an extracted folder) and where it goes (the SD card, or any folder), then
press Build. After downloading a new release, open it from the new folder and press Build again: the choices are
remembered and make_sd_cached.py's cache (outside the release folders) makes it a few minutes instead of half an hour.

It checks what the build needs first (devkitPro and its Switch packages, ccache, the Python modules) and can install
what is missing. Start it with "Build for Switch.bat" in the release folder, or: python switch_builder.py
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import webbrowser

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools", "switch"))
if not os.path.isfile(os.path.join(HERE, "tools", "switch", "make_sd.py")):
    tk.Tk().withdraw()
    messagebox.showerror("SwitchWakerHD", "Put switch_builder.py, make_sd_cached.py and \"Build for Switch.bat\" in "
                         "the unzipped SwitchWakerHD release folder, next to its tools folder.")
    sys.exit(1)
import make_sd  # noqa: E402
import make_sd_cached  # noqa: E402

ROOT = make_sd.ROOT
DKP = make_sd.DKP_WIN
CACHE = make_sd_cached.default_cache()
SETTINGS = os.path.join(CACHE, "builder.json")
DEFAULT_KEYS = os.path.join(os.path.expanduser("~"), ".switch", "prod.keys")
DEVKITPRO_URL = "https://github.com/devkitPro/installer/releases"
PACKAGES = ["switch-dev", "deko3d", "uam", "switch-lz4", "switch-zlib", "ccache", "gcc", "git"]
STEPS = {1: (0, 10), 2: (10, 12), 3: (12, 30), 4: (30, 92), 5: (92, 100)}  # step -> progress bar range
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---- what the build needs

def have_module(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def dkp(*parts):
    return os.path.join(DKP, *parts)


def checks():
    """[(name, ok, fix)]: fix is 'devkitpro', 'pacman' or 'pip:<module>', or None."""
    container = make_sd.have_container_engine()
    out = []
    if container:
        out.append(("Docker/Podman (builds in a container)", True, None))
    else:
        out.append(("devkitPro", os.path.isfile(dkp("msys2", "usr", "bin", "bash.exe")) and
                    os.path.isdir(dkp("devkitA64")), "devkitpro"))
        libs = [dkp("libnx", "lib", "libnx.a"), dkp("libnx", "lib", "libdeko3d.a"), dkp("tools", "bin", "uam.exe"),
                dkp("portlibs", "switch", "lib", "liblz4.a"), dkp("portlibs", "switch", "lib", "libz.a")]
        out.append(("Switch packages (libnx, deko3d, uam, lz4, zlib)", all(map(os.path.isfile, libs)), "pacman"))
        out.append(("ccache (fast updates)", os.path.isfile(dkp("msys2", "usr", "bin", "ccache.exe")), "pacman"))
    out.append(("zstandard (for .wua)", have_module("zstandard"), "pip:zstandard"))
    out.append(("pycryptodome (for .wux/.wud)", have_module("Crypto"), "pip:pycryptodome"))
    if not container:
        out.append(("Pillow + gcc (HOME-screen icon)", have_module("PIL") and
                    os.path.isfile(dkp("msys2", "usr", "bin", "gcc.exe")), "forwarder"))
    return out


LONG_KEYS = re.compile(r"rsa", re.I)  # RSA keys are longer than the 16/32-byte AES keys: not checked


def check_keys(path):
    """None when prod.keys looks well formed, else what is wrong (no key values are shown)."""
    try:
        with open(path, errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        return "cannot read it (%s)" % e.strerror
    bad, n = [], 0
    for line in lines:
        name, sep, value = line.strip().partition("=")
        name, value = name.strip(), value.strip()
        if not sep or name.startswith(("#", ";")):
            continue
        n += 1
        if not LONG_KEYS.search(name) and (len(value) not in (32, 64) or not re.fullmatch(r"[0-9a-fA-F]+", value)):
            bad.append(name)
    if not n:
        return "it has no keys in it"
    if bad:
        return ("%d keys have the wrong length (e.g. %s): the file is damaged or not a real dump. Dump it again from "
                "your console with Lockpick_RCM." % (len(bad), ", ".join(bad[:3])))
    return None


def looks_like_sd(path):
    root = os.path.splitdrive(os.path.abspath(path))[0] + os.sep
    return any(os.path.isdir(os.path.join(root, d)) for d in ("atmosphere", "switch", "Nintendo"))


def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(SETTINGS, "w") as f:
            json.dump(s, f, indent=1)
    except OSError:
        pass


# ---- the window

class Builder(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SwitchWakerHD - Build for Switch")
        self.minsize(640, 0)
        self.proc = None
        self.installing, self.queue_after, self.step, self.errors = False, None, 0, []
        self.lines = queue.Queue()
        s = load_settings()
        self.game = tk.StringVar(value=s.get("game", ""))
        self.out = tk.StringVar(value=s.get("out", ""))
        self.fwd = tk.BooleanVar(value=s.get("forwarder", os.path.isfile(DEFAULT_KEYS)))
        self.keys = tk.StringVar(value=s.get("keys", ""))
        self.status = tk.StringVar(value="Ready.")
        self.build_ui()
        self.refresh_checks()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.pump)

    def build_ui(self):
        pad = {"padx": 8, "pady": 4}
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Your game:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.game).grid(row=0, column=1, sticky="ew", **pad)
        b = ttk.Frame(f)
        b.grid(row=0, column=2, sticky="e")
        ttk.Button(b, text="File...", command=self.pick_game).pack(side="left")
        ttk.Button(b, text="Folder...", command=self.pick_game_dir).pack(side="left", padx=(4, 0))
        ttk.Label(f, text="A .wua (Cemu), a .wux/.wud with its .key, or an extracted game folder. "
                          "The USA or Europe game, version 0.", foreground="gray").grid(
            row=1, column=1, columnspan=2, sticky="w", padx=8)

        ttk.Label(f, text="Put it on:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.out).grid(row=2, column=1, sticky="ew", **pad)
        ttk.Button(f, text="Browse...", command=self.pick_out).grid(row=2, column=2, sticky="e", **pad)
        ttk.Label(f, text="Your SD card (e.g. E:\\) to write it there directly, or any folder. "
                          "Empty: build\\sd in this release folder.", foreground="gray").grid(
            row=3, column=1, columnspan=2, sticky="w", padx=8)

        ttk.Checkbutton(f, text="HOME-screen icon (forwarder .nsp) with my prod.keys:", variable=self.fwd).grid(
            row=4, column=0, columnspan=2, sticky="w", **pad)
        k = ttk.Frame(f)
        k.grid(row=5, column=1, columnspan=2, sticky="ew")
        k.columnconfigure(0, weight=1)
        ttk.Entry(k, textvariable=self.keys).grid(row=0, column=0, sticky="ew", padx=8)
        ttk.Button(k, text="Browse...", command=self.pick_keys).grid(row=0, column=1, padx=8)
        ttk.Label(f, text="Empty: %s" % DEFAULT_KEYS, foreground="gray").grid(
            row=6, column=1, columnspan=2, sticky="w", padx=8)

        self.checkbox = ttk.LabelFrame(f, text="What the build needs", padding=6)
        self.checkbox.grid(row=7, column=0, columnspan=3, sticky="ew", **pad)
        self.checkbox.columnconfigure(0, weight=1)

        row = ttk.Frame(f)
        row.grid(row=8, column=0, columnspan=3, sticky="ew", **pad)
        self.build_btn = ttk.Button(row, text="Build / Update", command=self.start)
        self.build_btn.pack(side="left")
        self.cancel_btn = ttk.Button(row, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=6)
        self.log_btn = ttk.Button(row, text="Show log", command=self.toggle_log)
        self.log_btn.pack(side="right")

        self.bar = ttk.Progressbar(f, maximum=100)
        self.bar.grid(row=9, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(f, textvariable=self.status).grid(row=10, column=0, columnspan=3, sticky="w", **pad)

        self.logframe = ttk.Frame(f)
        self.log = tk.Text(self.logframe, height=16, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(self.logframe, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.logrow = 11
        f.rowconfigure(self.logrow, weight=1)

    # -- pickers

    def pick_game(self):
        p = filedialog.askopenfilename(title="Your game", filetypes=[
            ("Wii U game", "*.wua *.wux *.wud"), ("All files", "*.*")])
        if p:
            self.game.set(os.path.normpath(p))

    def pick_game_dir(self):
        p = filedialog.askdirectory(title="An extracted game folder (code, content, meta)")
        if p:
            self.game.set(os.path.normpath(p))

    def pick_out(self):
        p = filedialog.askdirectory(title="Your SD card, or a folder")
        if p:
            self.out.set(os.path.normpath(p))

    def pick_keys(self):
        p = filedialog.askopenfilename(title="Your console's prod.keys", filetypes=[
            ("prod.keys", "*.keys"), ("All files", "*.*")])
        if p:
            self.keys.set(os.path.normpath(p))

    # -- checks and fixes

    def refresh_checks(self):
        for w in self.checkbox.winfo_children():
            w.destroy()
        self.missing = []
        for i, (name, ok, fix) in enumerate(checks()):
            ttk.Label(self.checkbox, text=("\u2714  " if ok else "\u2716  ") + name,
                      foreground="green" if ok else "firebrick").grid(row=i, column=0, sticky="w")
            if not ok:
                self.missing.append(name)
                if fix:
                    ttk.Button(self.checkbox, text="Get it" if fix == "devkitpro" else "Install",
                               command=lambda f=fix: self.fix(f)).grid(row=i, column=1, sticky="e", pady=1)

    def fix(self, what):
        if what == "devkitpro":
            if messagebox.askokcancel("devkitPro", "devkitPro (the Switch compiler) is installed with its own "
                                      "installer. Open its download page?\n\nInstall it to C:\\devkitPro with the "
                                      "Switch development option, then come back and press Install for the "
                                      "packages."):
                webbrowser.open(DEVKITPRO_URL)
            return
        if what in ("pacman", "forwarder"):
            msg = "Install with devkitPro's package manager:\n\n" + " ".join(PACKAGES)
            cmd = [dkp("msys2", "usr", "bin", "bash.exe"), "-l", "-c",
                   "pacman -S --noconfirm --needed " + " ".join(PACKAGES)]
            if what == "forwarder" and not have_module("PIL"):
                self.queue_after = [sys.executable, "-m", "pip", "install", "--user", "pillow"]
        else:
            mod = what.split(":", 1)[1]
            msg = "Install the Python module %s (pip)?" % mod
            cmd = [sys.executable, "-m", "pip", "install", "--user", mod]
        if messagebox.askokcancel("Install", msg):
            self.run(cmd, "Installing...", installing=True)

    # -- running

    def args(self):
        g = self.game.get().strip().strip('"')
        if not g:
            raise ValueError("Pick your game first.")
        if os.path.isdir(g):
            a = ["--game-dir", g]
        elif os.path.isfile(g):
            ext = os.path.splitext(g)[1].lower()
            if ext == ".wua":
                a = ["--wua", g]
            elif ext in (".wux", ".wud"):
                a = ["--image", g]
            else:
                raise ValueError("The game must be a .wua, .wux or .wud file, or an extracted folder.")
        else:
            raise ValueError("The game was not found:\n" + g)
        out = self.out.get().strip().strip('"')
        if out:
            if not os.path.isdir(out):
                raise ValueError("The output folder does not exist:\n" + out)
            if os.path.splitdrive(os.path.abspath(out))[1] in ("\\", "/") and not looks_like_sd(out):
                if not messagebox.askyesno("Output", "%s does not look like a Switch SD card (no atmosphere or "
                                           "switch folder). Write switch\\wwhd there anyway?" % out):
                    raise ValueError(None)
            a += ["--out", out]
        if self.fwd.get():
            keys = self.keys.get().strip().strip('"') or DEFAULT_KEYS
            if not os.path.isfile(keys):
                raise ValueError("No prod.keys at:\n%s\n\nPick yours, or untick the HOME-screen icon." % keys)
            problem = check_keys(keys)
            if problem:
                raise ValueError("Your prod.keys (%s) cannot be used: %s\n\nPick another one, or untick the "
                                 "HOME-screen icon." % (keys, problem))
            a += ["--keys", keys]
        else:
            a += ["--no-forwarder"]
        return a

    def start(self):
        if self.missing and not messagebox.askyesno("Missing", "Not everything the build needs is there:\n\n" +
                                                    "\n".join(self.missing) + "\n\nBuild anyway?"):
            return
        try:
            a = self.args()
        except ValueError as e:
            if e.args[0]:
                messagebox.showerror("SwitchWakerHD", e.args[0])
            return
        save_settings({"game": self.game.get(), "out": self.out.get(), "forwarder": self.fwd.get(),
                       "keys": self.keys.get()})
        self.bar["value"] = 0
        self.step = 0
        self.errors = []
        self.run([sys.executable, "-u", os.path.join(HERE, "make_sd_cached.py")] + a, "Building...")

    def run(self, cmd, status, installing=False):
        self.installing = installing
        self.set_busy(True)
        self.status.set(status)
        self.write("\n$ " + " ".join(cmd) + "\n")
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        threading.Thread(target=self.reader, args=(self.proc,), daemon=True).start()

    def reader(self, proc):
        for raw in iter(proc.stdout.readline, b""):
            self.lines.put(raw.decode("utf-8", "replace"))
        self.lines.put((proc.wait(),))

    def pump(self):
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    self.finished(item[0])
                else:
                    self.write(item)
                    self.progress(item)
        except queue.Empty:
            pass
        self.after(100, self.pump)

    def progress(self, line):
        if self.installing:
            return
        m = re.match(r"\[(\d)/5\] (.*)", line)
        if m:
            self.step = int(m.group(1))
            self.status.set("Step %s of 5: %s" % (m.group(1), m.group(2).strip()))
            self.bar["value"] = STEPS[self.step][0]
            return
        if "from the cache" in line:
            self.status.set(self.status.get() + " - from the cache")
        m = re.match(r"\[\s*(\d+)%\]", line)
        if m and self.step == 4:
            lo, hi = STEPS[4]
            self.bar["value"] = max(self.bar["value"], lo + (hi - lo) * int(m.group(1)) / 100)
        if line.startswith("make_sd:") or line.lstrip().startswith(("WARNING", "error:")):
            self.errors.append(line.strip())

    def finished(self, code):
        self.proc = None
        self.set_busy(False)
        if self.installing:
            nxt = getattr(self, "queue_after", None)
            self.queue_after = None
            if code == 0 and nxt:
                self.run(nxt, "Installing...", installing=True)
                return
            self.status.set("Installed." if code == 0 else "The install failed (see the log).")
            if code:
                self.show_log()
            self.refresh_checks()
            return
        if code == 0:
            self.bar["value"] = 100
            out = self.out.get().strip() or os.path.join(ROOT, "build", "sd")
            self.status.set("Done: " + out)
            on_sd = self.out.get().strip() and looks_like_sd(out)
            messagebox.showinfo("SwitchWakerHD", "Done.\n\n" + (
                "It is on your SD card (switch\\wwhd). Put the card back in the Switch, then hold R while starting "
                "any game to open the Homebrew Menu and pick SwitchWakerHD." if on_sd else
                "Copy the contents of\n%s\nto the root of your SD card." % out) +
                ("\n\nWarnings:\n" + "\n".join(self.errors[-3:]) if self.errors else ""))
        else:
            self.status.set("The build stopped (see the log).")
            self.show_log()
            why = [e for e in self.errors if e.startswith("make_sd:")]
            messagebox.showerror("SwitchWakerHD", (why[-1][len("make_sd:"):].strip() if why else
                                                   "The build stopped. The log shows why."))

    def cancel(self):
        if self.proc and messagebox.askyesno("Cancel", "Stop the build? The next build continues where it can."):
            self.cancel_now()

    def on_close(self):
        if self.proc:
            if not messagebox.askyesno("Quit", "A build is running. Stop it and quit?"):
                return
            self.cancel_now()
        self.destroy()

    def cancel_now(self):
        if os.name == "nt":
            subprocess.call(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], creationflags=NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.proc.terminate()

    # -- small helpers

    def set_busy(self, busy):
        self.build_btn.configure(state="disabled" if busy else "normal")
        self.cancel_btn.configure(state="normal" if busy and not self.installing else "disabled")

    def write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def show_log(self):
        if not self.logframe.winfo_ismapped():
            self.toggle_log()

    def toggle_log(self):
        if self.logframe.winfo_ismapped():
            self.logframe.grid_remove()
            self.log_btn.configure(text="Show log")
        else:
            self.logframe.grid(row=self.logrow, column=0, columnspan=3, sticky="nsew", padx=8, pady=4)
            self.log_btn.configure(text="Hide log")


if __name__ == "__main__":
    Builder().mainloop()
