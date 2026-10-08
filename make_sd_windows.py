#!/usr/bin/env python3
"""make_sd_windows.py: build SwitchWakerHD on Windows with a native devkitPro install (no Docker, no WSL).

A drop-in replacement for tools/switch/make_sd.py + tools/switch/build.sh of the SwitchWakerHD release
(https://github.com/centollOS/SwitchWakerHD). Put this file in the unzipped release folder (next to
README.md and tools/) and run it from there. It changes nothing in the release.

  python make_sd_windows.py --wua "C:\\path\\Wind Waker HD (US).wua"
  python make_sd_windows.py --image GAME.wux          (GAME.key and common.key next to it; needs pycryptodome)
  python make_sd_windows.py --game-dir C:\\path\\10143500   (an extracted folder with code/, content/, meta/)

options: --out DIR            where the SD card folder goes (default build\\sd)
         --jobs N             parallel compiles (each can need ~1.5 GB of RAM; default: half the CPUs)
         --strict             stop (not just warn) if the release is not one this script was tested with
         --reuse-translation  keep build\\gen from an earlier run instead of translating the game code again

Steps: extract the game, check it is version 0 of the USA game, translate the PowerPC code to C
(tools/recomp/recomp.py), compile with devkitPro's devkitA64 through its MSYS2, write build\\sd.

Needs: Windows 10/11, Python 3 (pip install zstandard for --wua), devkitPro with the Switch libraries (see
README.md). What this builds contains the game: for your own console only, do not share it.

License: MPL-2.0, like SwitchWakerHD. The .wua reader is a Python port of tools/wudextract/zarchive.cpp
from the release (ZArchive format).
"""
import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.abspath(__file__))
DKP = os.environ.get("DEVKITPRO_WIN", "C:/devkitPro")
TITLE = "0005000010143500"

# SHA-256 (line endings normalized to LF) of the release files this script depends on, per release it was
# tested with. To add a release: build with it, then add its hashes (python make_sd_windows.py --print-hashes).
TESTED = {
    "v0.2.0": {
        "tools/installer/setup.py": "00055121872d73ebf6926ab81fef41a359b03af81bd78e5b08986cebf8b1363b",
        "tools/recomp/recomp.py": "a4cd7c8472a6ab56b75f0b0d911c959adec5e5dd7837b7ab6fe3f8f3cb6f8704",
        "CMakeLists.txt": "b55370431c2b0049a84496e1eeef54a3a997dd95277cf7142cc3133327a5bac3",
        "tools/switch/build.sh": "0a61bd3704933181929918f3bd2262bea478a99f7049e41d3fe60e4d2d07ab57",
    },
}


def fail(msg):
    sys.exit("\nmake_sd_windows: " + msg)


def step(n, text):
    print("\n[%d/5] %s" % (n, text), flush=True)


def run(cmd, env=None):
    print("  $ " + " ".join(cmd), flush=True)
    if subprocess.call(cmd, cwd=ROOT, env=env) != 0:
        fail("this step failed (see the messages above)")


# ---------------------------------------------------------------------------------------------
# .wua (Cemu ZArchive) reader

MAGIC, VERSION1, FOOTER, BLOCK = 0x169F52D6, 0x61BF3A01, 144, 65536


def be(b):
    return int.from_bytes(b, "big")


class Zar:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.f.seek(0, 2)
        self.size = self.f.tell()
        if self.size <= FOOTER:
            fail("not a Cemu Wii U archive (.wua): too small")
        ft = self.read(self.size - FOOTER, FOOTER)
        if be(ft[140:144]) != MAGIC or be(ft[136:140]) != VERSION1:
            fail("not a Cemu Wii U archive (.wua)")
        if be(ft[128:136]) != self.size:
            fail("the archive is truncated (size mismatch): incomplete download or copy?")
        self.hash = ft[96:128]
        sec = [(be(ft[16 * i:16 * i + 8]), be(ft[16 * i + 8:16 * i + 16])) for i in range(6)]
        self.data_off = sec[0][0]
        rec = self.read(*sec[1])
        self.boff, self.blen = [], []
        for r in range(len(rec) // 40):
            q = rec[r * 40:r * 40 + 40]
            off = be(q[:8])
            for k in range(16):
                ln = be(q[8 + 2 * k:10 + 2 * k]) + 1
                self.boff.append(off)
                self.blen.append(ln)
                off += ln
        names = self.read(*sec[2])
        tree = self.read(*sec[3])
        self.nodes = []  # (name, is_file, a, b): file: offset, size; folder: first child, child count
        for i in range(len(tree) // 16):
            w0, w1, w2, w3 = struct.unpack(">IIII", tree[i * 16:i * 16 + 16])
            is_file, no, name = w0 >> 31, w0 & 0x7FFFFFFF, ""
            if i:
                ln = names[no] & 0x7F
                if names[no] & 0x80:
                    ln |= names[no + 1] << 7
                    no += 2
                else:
                    no += 1
                raw = names[no:no + ln]
                try:
                    name = raw.decode("utf-8")
                except UnicodeDecodeError:
                    name = raw.decode("latin-1")
            if is_file:
                self.nodes.append((name, True, w1 | (w3 & 0xFFFF) << 32, w2 | (w3 & 0xFFFF0000) << 16))
            else:
                self.nodes.append((name, False, w1, w2))
        import zstandard
        self.dz = zstandard.ZstdDecompressor()
        self.cached = None, None

    def read(self, off, n):
        self.f.seek(off)
        d = self.f.read(n)
        if len(d) != n:
            fail("the archive is truncated or unreadable")
        return d

    def block(self, i):
        if self.cached[0] == i:
            return self.cached[1]
        raw = self.read(self.data_off + self.boff[i], self.blen[i])
        if self.blen[i] != BLOCK:  # a block of exactly BLOCK bytes is stored uncompressed
            raw = self.dz.decompress(raw, max_output_size=BLOCK)
            if len(raw) != BLOCK:
                fail("block %d of the archive is damaged" % i)
        self.cached = i, raw
        return raw

    def write_file(self, node, dst):
        _, _, pos, remaining = self.nodes[node]
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as out:
            while remaining > 0:
                w = pos % BLOCK
                n = min(remaining, BLOCK - w)
                out.write(self.block(pos // BLOCK)[w:w + n])
                pos += n
                remaining -= n

    def walk(self, d, rel=""):
        first, count = self.nodes[d][2:]
        for c in range(first, first + count):
            name, is_file = self.nodes[c][:2]
            if is_file:
                yield rel + name, c
            else:
                yield from self.walk(c, rel + name + "/")

    def verify(self):
        h, pos, at = hashlib.sha256(), 0, self.size - FOOTER + 96
        self.f.seek(0)
        while pos < self.size:
            buf = bytearray(self.f.read(min(4 << 20, self.size - pos)))
            for i in range(max(pos, at), min(pos + len(buf), at + 32)):
                buf[i - pos] = 0  # the hash field counts as zeros
            h.update(buf)
            pos += len(buf)
        return h.digest() == self.hash


def extract_wua(archive, dst):
    try:
        import zstandard  # noqa: F401
    except ImportError:
        fail("reading a .wua needs zstandard: python -m pip install zstandard")
    z = Zar(archive)
    first, count = z.nodes[0][2:]
    found = []
    for c in range(first, first + count):
        m = re.fullmatch(r"([0-9a-fA-F]{16})_v(\d+)", z.nodes[c][0])
        if m and not z.nodes[c][1]:
            found.append((m.group(1).lower(), int(m.group(2)), z.nodes[c][0], c))
    cand = [t for t in found if t[0] == TITLE]
    if not cand:
        fail("this archive has no The Wind Waker HD (USA) base game, title %s (it contains: %s)"
             % (TITLE, ", ".join(t[2] for t in found) or "no Wii U titles"))
    t = max(cand, key=lambda x: x[1])
    print("  checking the archive's SHA-256 ...", flush=True)
    if not z.verify():
        fail("the archive's SHA-256 does not match: it is damaged")
    files = list(z.walk(t[3]))
    print("  extracting %s (%d files) ..." % (t[2], len(files)), flush=True)
    for i, (p, c) in enumerate(files):
        z.write_file(c, os.path.join(dst, *p.split("/")))
        if i % 250 == 0:
            print("    %d/%d" % (i, len(files)), flush=True)


# ---------------------------------------------------------------------------------------------
# steps


def release_hashes():
    out = {}
    for rel in TESTED["v0.2.0"]:
        try:
            with open(os.path.join(ROOT, *rel.split("/")), "rb") as f:
                out[rel] = hashlib.sha256(f.read().replace(bytes([13, 10]), bytes([10]))).hexdigest()
        except OSError:
            out[rel] = None
    return out


def check_release(strict):
    """Warns (or stops with --strict) when the release differs from every release this script was tested with."""
    got = release_hashes()
    for ver, want in TESTED.items():
        if got == want:
            print("  release files match %s, which this script was tested with" % ver)
            return
    best = max(TESTED, key=lambda v: sum(got[k] == h for k, h in TESTED[v].items()))
    changed = [k for k, h in TESTED[best].items() if got[k] != h]
    msg = ("this release differs from the tested %s (changed: %s). The build may fail. If it does, look for an "
           "updated make_sd_windows.py at https://github.com/digdat0/SwitchWakerHD-windows-native-build"
           % (best, ", ".join(changed)))
    if strict:
        fail(msg)
    print("  WARNING: " + msg, flush=True)


def check_devkitpro():
    need = {
        "devkitA64 compiler": DKP + "/devkitA64/bin/aarch64-none-elf-gcc.exe",
        "libnx": DKP + "/libnx/lib/libnx.a",
        "deko3d": DKP + "/libnx/lib/libdeko3d.a",
        "uam (shader compiler)": DKP + "/tools/bin/uam.exe",
        "elf2nro": DKP + "/tools/bin/elf2nro.exe",
        "Switch.cmake": DKP + "/cmake/Switch.cmake",
        "switch-zlib": DKP + "/portlibs/switch/lib/libz.a",
        "switch-lz4": DKP + "/portlibs/switch/lib/liblz4.a",
        "devkitPro MSYS2 bash": DKP + "/msys2/usr/bin/bash.exe",
    }
    missing = [k for k, v in need.items() if not os.path.exists(v)]
    if missing:
        fail("devkitPro is not set up at %s (missing: %s).\nSee README.md: install devkitPro, then in its MSYS2 "
             "run: pacman -S switch-dev deko3d uam switch-lz4 switch-zlib" % (DKP, ", ".join(missing)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--wua", help="a Cemu Wii U archive (.wua), no keys needed")
    src.add_argument("--image", help="a disc image (.wux/.wud), with GAME.key and common.key")
    src.add_argument("--game-dir", help="an extracted game folder (code/, content/, meta/)")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "sd"))
    ap.add_argument("--jobs", type=int)
    ap.add_argument("--reuse-translation", action="store_true")
    ap.add_argument("--strict", action="store_true", help="stop, not just warn, if the release is not a tested one")
    ap.add_argument("--print-hashes", action="store_true", help="print this release's file hashes and exit")
    args = ap.parse_args()
    if args.print_hashes:
        for k, v in release_hashes().items():
            print('        "%s": "%s",' % (k, v))
        return

    if not os.path.isfile(os.path.join(ROOT, "tools", "recomp", "recomp.py")):
        fail("put this file in the unzipped SwitchWakerHD release folder (the one with tools\\ and README.md)")
    check_devkitpro()
    check_release(args.strict)
    sys.path.insert(0, os.path.join(ROOT, "tools", "installer"))
    import setup  # the release's own game checks

    step(1, "the game files")
    if args.game_dir:
        game = os.path.abspath(args.game_dir)
    else:
        game = os.path.join(ROOT, "build", "sd-game")
        tmp = game + ".partial"
        shutil.rmtree(tmp, ignore_errors=True)
        if args.wua:
            extract_wua(os.path.abspath(args.wua), tmp)
        else:
            image = os.path.abspath(args.image)
            run([sys.executable, "-I", os.path.join(ROOT, "tools", "wudextract.py"), image, "extract", tmp])
        if not setup.valid_game_folder(tmp):
            fail("the extracted files are incomplete (no code/cking.rpx, content/ or meta/meta.xml)")
        shutil.rmtree(game, ignore_errors=True)
        os.replace(tmp, game)
    if not setup.valid_game_folder(game):
        fail("%s is not an extracted game (code/cking.rpx, content/, meta/meta.xml)" % game)
    print("  " + game)

    step(2, "checking the game version")
    try:
        setup.check_game_version(game)
    except setup.SetupError as e:
        fail(str(e))
    print("  The Wind Waker HD (USA), version 0: OK")

    step(3, "translating the game code to C (a few minutes)")
    gen = os.path.join(ROOT, "build", "gen")
    have_gen = os.path.isdir(gen) and any(f.startswith("code_") for f in os.listdir(gen))
    if args.reuse_translation and have_gen:
        print("  reusing build\\gen")
    else:
        shutil.rmtree(gen, ignore_errors=True)
        run([sys.executable, "-I", os.path.join(ROOT, "tools", "recomp", "recomp.py"),
             os.path.join(game, "code", "cking.rpx"), gen])

    step(4, "compiling the Switch homebrew with devkitPro (10-30 minutes the first time)")
    jobs = args.jobs or max(1, (os.cpu_count() or 2) // 2)
    env = dict(os.environ, DEVKITPRO="/opt/devkitpro", MSYS2_PATH_TYPE="inherit", WWHD_ROOT=ROOT, WWHD_JOBS=str(jobs))
    # Unix Makefiles, not Ninja (devkitPro's MSYS2 cmake and a Windows ninja disagree about paths);
    # no compiler dependency files (the Windows-native compiler writes C:/ paths that make cannot parse);
    # uam writes into dksh/, which nothing creates.
    script = r'''set -e
cd "$(cygpath -u "$WWHD_ROOT")"
export DEVKITA64=$DEVKITPRO/devkitA64
export PATH="$DEVKITPRO/tools/bin:$DEVKITA64/bin:$PATH"
dir=build/switch-dk
mkdir -p $dir/dksh
cmake -S . -B $dir -G "Unix Makefiles" -DCMAKE_DEPENDS_USE_COMPILER=OFF \
      -DCMAKE_TOOLCHAIN_FILE=$DEVKITPRO/cmake/Switch.cmake -DCMAKE_BUILD_TYPE=Release \
      -DWWHD_RENDERER=DEKO3D -DWWHD_DEKO3D_DEBUG_LIB=OFF
cmake --build $dir -j $WWHD_JOBS
'''
    run([DKP + "/msys2/usr/bin/bash.exe", "-l", "-c", script], env=env)
    nro = os.path.join(ROOT, "build", "switch-dk", "wwhd.nro")
    if not os.path.isfile(nro):
        fail("the build made no %s" % nro)

    step(5, "the SD card folder")
    out = os.path.abspath(args.out)
    wwhd = os.path.join(out, "switch", "wwhd")
    os.makedirs(wwhd, exist_ok=True)
    shutil.copyfile(nro, os.path.join(wwhd, "wwhd.nro"))
    gdst = os.path.join(wwhd, "game")
    if os.path.realpath(gdst) != os.path.realpath(game):
        shutil.rmtree(gdst, ignore_errors=True)
        for part in ("code", "content", "meta"):
            shutil.copytree(os.path.join(game, part), os.path.join(gdst, part))
    print("""
Done: %s

Copy the contents of that folder to the root of the SD card (switch/wwhd/ ends up at sdmc:/switch/wwhd/).
Then hold R while starting any installed game to open the Homebrew Menu in title mode, and pick
SwitchWakerHD. These files contain the game: for your own console only; do not share them.""" % out)


if __name__ == "__main__":
    main()
