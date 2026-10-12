#!/usr/bin/env python3
"""SwitchWakerHD: the release's tools/switch/make_sd.py with a cache that outlives the release folder, so a new release does not rebuild
everything.

The same five steps as make_sd.py (and the same options), but:

  1. the extracted game is kept in the cache: it is extracted once per dump (keyed on the archive's name, size and
     modification time), not once per release folder;
  3. the recompiler's output is kept in the cache, keyed on a hash of code/cking.rpx, tools/recomp/, tools/rpx.py and
     the WWHD_RECOMP_* variables: when none of them changed, the recompiler does not run. build/gen/ is then updated
     file by file, so the files that did not change keep their dates and are not compiled again;
  4. a native build compiles through ccache (devkitPro MSYS2: pacman -S ccache), with its cache in the same folder:
     a new release folder gets the objects of every file that compiles the same. The first build with the cache fills
     it (a full build, once); a build/switch-dk/ made without ccache is rebuilt once for that;
  5. the SD card folder's game/ is updated file by file instead of copied again.

The cache: --cache DIR, or %LOCALAPPDATA%\\SwitchWakerHD (Linux/macOS: ~/.cache/SwitchWakerHD). It holds the game:
it is for your own computer only, like build/. Deleting it is always safe (the next build is a full one).

usage: the same as make_sd.py, e.g.
  python make_sd_cached.py --wua GAME.wua [--out DIR] [--cache DIR] [--no-ccache]
"""
import argparse
import filecmp
import hashlib
import os
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
_RELEASE = os.path.dirname(os.path.abspath(__file__))
if not os.path.isfile(os.path.join(_RELEASE, "tools", "switch", "make_sd.py")):
    sys.exit("make_sd_cached: put this file in the unzipped SwitchWakerHD release folder, next to its tools\ folder")
sys.path.insert(0, os.path.join(_RELEASE, "tools", "switch"))
import make_sd  # noqa: E402  (its steps, checks and helpers: this script only adds the cache around them)
if not hasattr(make_sd, "native_shell"):
    sys.exit("make_sd_cached: this release's make_sd.py has no native devkitPro build: it needs SwitchWakerHD v0.4.3 or "
             "newer (for older releases use make_sd_windows.py)")
from make_sd import ROOT, fail, run, setup, step  # noqa: E402

CACHE_VERSION = "1"  # part of every key: raise it when what is cached changes
KEEP_GAMES, KEEP_GENS = 2, 3  # the newest entries kept in the cache (a game is a few GB, a recompile ~300 MB)
CCACHE_SIZE = "20G"


def default_cache():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "SwitchWakerHD")


def prune(folder, keep):
    """Keeps the `keep` most recently used entries of folder (use() touches an entry)."""
    entries = [os.path.join(folder, n) for n in os.listdir(folder) if not n.endswith(".partial")]
    for old in sorted(entries, key=os.path.getmtime, reverse=True)[keep:]:
        print("  cache: removing an old entry, " + old, flush=True)
        shutil.rmtree(old, ignore_errors=True)


def use(path):
    os.utime(path, None)
    return path


def file_hash(h, path):
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)


# ---- step 1: the game, extracted once per dump

def cached_game(args, cache):
    archive = os.path.abspath(args.image or args.wua)
    if not os.path.isfile(archive):
        fail("%s was not found" % archive)
    st = os.stat(archive)
    # not the archive's contents (several GB to read): its name, size and date, which change with another dump
    key = hashlib.sha256(("%s|%s|%d|%d" % (CACHE_VERSION, os.path.basename(archive).lower(), st.st_size,
                                           st.st_mtime_ns // 1000000000)).encode()).hexdigest()[:16]
    games = os.path.join(cache, "game")
    dst = os.path.join(games, key)
    if setup.valid_game_folder(dst):
        print("  from the cache (not extracted again): " + dst)
        return use(dst)
    os.makedirs(games, exist_ok=True)
    got = make_sd.extract(archive) if args.image else make_sd.extract_wua(archive)  # -> build/sd-game
    shutil.rmtree(dst, ignore_errors=True)
    shutil.move(got, dst)
    prune(games, KEEP_GAMES)
    return dst


# ---- step 3: the recompiler's output, kept per input

def recomp_key(game):
    h = hashlib.sha256(CACHE_VERSION.encode())
    file_hash(h, os.path.join(game, "code", "cking.rpx"))
    tools = os.path.join(ROOT, "tools")
    inputs = [os.path.join(tools, "rpx.py")]
    for d, dirs, files in os.walk(os.path.join(tools, "recomp")):
        dirs[:] = sorted(x for x in dirs if x != "__pycache__")
        inputs += [os.path.join(d, f) for f in sorted(files) if not f.endswith(".pyc")]
    for p in inputs:
        h.update(os.path.relpath(p, ROOT).replace(os.sep, "/").encode() + b"\0")
        file_hash(h, p)
    for k in sorted(k for k in os.environ if k.startswith("WWHD_RECOMP_")):
        h.update(("%s=%s\0" % (k, os.environ[k])).encode())
    return h.hexdigest()[:16]


def sync_tree(src, dst, same=None):
    """Makes dst a copy of src, touching only the files that differ: an unchanged file keeps its date (so make does
    not compile it again). same(a, b) says whether two files are the same (default: their bytes)."""
    same = same or (lambda a, b: filecmp.cmp(a, b, shallow=False))
    changed = removed = 0
    for d, dirs, files in os.walk(src):
        rel = os.path.relpath(d, src)
        out = os.path.normpath(os.path.join(dst, rel))
        os.makedirs(out, exist_ok=True)
        for f in files:
            a, b = os.path.join(d, f), os.path.join(out, f)
            if not (os.path.isfile(b) and same(a, b)):
                shutil.copy2(a, b)
                changed += 1
    for d, dirs, files in os.walk(dst, topdown=False):
        rel = os.path.relpath(d, dst)
        for f in files:
            if not os.path.isfile(os.path.join(src, rel, f)):
                os.remove(os.path.join(d, f))
                removed += 1
        if not os.path.isdir(os.path.join(src, rel)):
            os.rmdir(d)
    return changed, removed


def cached_recomp(game, cache):
    gen = os.path.join(ROOT, "build", "gen")
    if os.path.islink(gen):
        fail("build/gen is a symbolic link: remove it (or run from a checkout without one)")
    gens = os.path.join(cache, "gen")
    os.makedirs(gens, exist_ok=True)
    key = recomp_key(game)
    out = os.path.join(gens, key)
    if os.path.isfile(os.path.join(out, "table.c")):
        print("  from the cache (the recompiler and the game code did not change): " + out)
    else:
        tmp = out + ".partial"
        shutil.rmtree(tmp, ignore_errors=True)
        run([sys.executable, "-I", os.path.join(ROOT, "tools", "recomp", "recomp.py"),
             os.path.join(game, "code", "cking.rpx"), tmp])
        os.replace(tmp, out)
        prune(gens, KEEP_GENS)
    changed, removed = sync_tree(use(out), gen)
    print("  build/gen: %d files changed, %d removed (the others keep their dates)" % (changed, removed))


# ---- step 4: the build, through ccache

def msys_path(path):
    """path as devkitPro's MSYS2 tools see it (CMake there writes /home/... paths)."""
    env = dict(os.environ)
    shell = make_sd.native_shell(env)
    return subprocess.check_output(shell + ["-c", 'cygpath -u "$0"', path], env=env, cwd=ROOT).decode().strip()


def ccache_env(env, cache, native):
    """Sets env for a build through ccache; False when it is not used."""
    if not native:
        print("  ccache: not used in the container build (the devkitpro image has none)")
        return False
    exe = os.path.join(make_sd.DKP_WIN, "msys2", "usr", "bin", "ccache.exe") if os.name == "nt" else \
        shutil.which("ccache")
    if not exe or not os.path.isfile(exe):
        print("  ccache: not installed, so every file is compiled (devkitPro > MSYS2: pacman -S ccache)")
        return False
    tr = msys_path if os.name == "nt" else (lambda p: p)
    ccdir = os.path.join(cache, "ccache")
    os.makedirs(ccdir, exist_ok=True)
    launcher = "/usr/bin/ccache" if os.name == "nt" else exe
    env.update({
        # read by CMake when it configures build/switch-dk the first time
        "CMAKE_C_COMPILER_LAUNCHER": launcher, "CMAKE_CXX_COMPILER_LAUNCHER": launcher,
        "CCACHE_DIR": tr(ccdir),
        # paths inside the release folder are hashed relative to it, so another release folder hits the same entries
        "CCACHE_BASEDIR": tr(ROOT), "CCACHE_NOHASHDIR": "1",
        "CCACHE_MAXSIZE": CCACHE_SIZE,
    })
    build = os.path.join(ROOT, "build", "switch-dk")
    cmcache = os.path.join(build, "CMakeCache.txt")
    if os.path.isfile(cmcache):
        with open(cmcache, errors="replace") as f:
            has = "CMAKE_C_COMPILER_LAUNCHER:" in f.read()
        if not has:
            # its objects were not made through ccache, so the cache would stay empty: build once more, filling it
            print("  ccache: build/switch-dk was made without ccache: rebuilding it once to fill the cache")
            shutil.rmtree(build)
    return True


def ccache_stats(env):
    shell = make_sd.native_shell(dict(env)) if os.name == "nt" else ["bash"]
    subprocess.call(shell + ["-c", "ccache --show-stats | grep -E 'Hits|Misses|Cache size' | head -4"], env=env,
                    cwd=ROOT)


# ---- step 5 + the forwarder

def same_meta(a, b):
    sa, sb = os.stat(a), os.stat(b)
    return sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)


def forwarder(args, out, game):
    """make_sd.forwarder, with the icon of the cached game (it looks in build/ by default)."""
    print("\n[+] HOME-screen forwarder (optional)", flush=True)
    keys = os.path.abspath(args.keys or os.path.join(os.path.expanduser("~"), ".switch", "prod.keys"))
    script = os.path.join(ROOT, "tools", "switch", "forwarder", "build_forwarder_windows.py")
    if not os.path.isfile(keys):
        print("  skipped: no prod.keys at %s (or use --keys PATH, outside this folder)" % keys)
    elif subprocess.call([sys.executable, "-I", script, "--keys", keys, "--sd", out,
                          "--icon", os.path.join(game, "meta", "iconTex.tga")], cwd=ROOT) != 0:
        print("  WARNING: the forwarder was not built (see above); wwhd.nro and the SD folder are fine.", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--image", help="the disc image (.wux/.wud)")
    src.add_argument("--wua", help="a Cemu Wii U archive (.wua; no keys needed)")
    src.add_argument("--game-dir", help="an extracted game folder (code/, content/, meta/): used in place")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "sd"), help="where the SD card folder goes")
    ap.add_argument("--cache", default=default_cache(), help="the cache folder (default: %(default)s)")
    ap.add_argument("--no-ccache", action="store_true", help="compile every file (no ccache)")
    ap.add_argument("--shaders", help="your console's shader_manifest.bin (or a shadercache_gl.bin)")
    ap.add_argument("--jobs", type=int, help="parallel compiles")
    ap.add_argument("--keys", help="native build: your console's prod.keys, for the HOME-screen forwarder")
    ap.add_argument("--no-forwarder", action="store_true", help="native build: do not build the forwarder (.nsp)")
    args = ap.parse_args()
    if not (args.image or args.wua or args.game_dir):
        ap.error("one of --image, --wua or --game-dir is required")
    cache = os.path.abspath(args.cache)
    if os.path.normcase(cache).startswith(os.path.normcase(ROOT) + os.sep):
        fail("keep the cache outside the release folder (--cache): it is meant to outlive it")
    native = not make_sd.have_container_engine()
    if native and not make_sd.native_toolchain_ok():
        fail("Docker or Podman is needed, or a native devkitPro with the Switch libraries: see INSTALL.md")
    print("cache: " + cache)

    step(1, "the game files")
    game = os.path.abspath(args.game_dir) if args.game_dir else cached_game(args, cache)
    if not setup.valid_game_folder(game):
        fail("%s is not an extracted game (code/cking.rpx, content/, meta/meta.xml)" % game)
    print("  " + game)

    step(2, "checking the game version")
    try:
        build = setup.check_game_version(game)
    except setup.SetupError as e:
        fail(str(e))
    print("  The Wind Waker HD (%s), version 0: OK" % build.name)

    step(3, "translating the game code to C (a few minutes, unless it is in the cache)")
    cached_recomp(game, cache)

    step(4, "building the Switch homebrew (only what changed)")
    env = dict(os.environ)
    if args.jobs:
        env["WWHD_JOBS"] = str(args.jobs)
    if native:
        env["WWHD_NATIVE"] = "1"
    cc = not args.no_ccache and ccache_env(env, cache, native)
    run((make_sd.native_shell(env) if native else ["bash"]) + ["tools/switch/build.sh"], env=env)
    if cc:
        ccache_stats(env)
    nro = os.path.join(ROOT, "build", "switch-dk", "wwhd.nro")
    if not os.path.isfile(nro):
        fail("the build made no %s" % nro)
    dksh = make_sd.shader_cache(os.path.abspath(args.shaders), game) if args.shaders else None

    step(5, "the SD card folder (only the files that changed)")
    out = os.path.abspath(args.out)
    wwhd = os.path.join(out, "switch", "wwhd")
    os.makedirs(wwhd, exist_ok=True)
    shutil.copyfile(nro, os.path.join(wwhd, "wwhd.nro"))
    if dksh:
        shutil.copyfile(dksh, os.path.join(wwhd, "shadercache_dksh.bin"))
    gdst = os.path.join(wwhd, "game")
    if os.path.realpath(gdst) != os.path.realpath(game):
        for part in ("code", "content", "meta"):
            changed, removed = sync_tree(os.path.join(game, part), os.path.join(gdst, part), same_meta)
            print("  game/%s: %d files copied, %d removed" % (part, changed, removed))
        for extra in set(os.listdir(gdst)) - {"code", "content", "meta"}:
            p = os.path.join(gdst, extra)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    if native and not args.no_forwarder:
        forwarder(args, out, game)
    print("""
Done: %s

Copy the contents of that folder to the root of the SD card (switch/wwhd/ ends up at
sdmc:/switch/wwhd/). If the card already has this game/ folder, only switch/wwhd/wwhd.nro (and the
files listed as copied above) changed. Saves and settings already on the card are kept.

These files contain the game: they are for your own console only; do not share them.""" % out)


if __name__ == "__main__":
    main()
