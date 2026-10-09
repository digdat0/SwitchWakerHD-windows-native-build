# SwitchWakerHD: build on Windows without Docker

A workaround for [SwitchWakerHD](https://github.com/centollOS/SwitchWakerHD) users on Windows who would rather
not set up Docker Desktop and WSL. It is **one script**, `make_sd_windows.py`, that does what the release's
`make_sd.py` does but compiles with a native [devkitPro](https://devkitpro.org) install instead of the
`devkitpro/devkita64` container.

This repo contains no SwitchWakerHD code and no game files. You bring both:

- the SwitchWakerHD release, from the project's own page;
- your own dump of **The Wind Waker HD, USA (00050000-10143500), version 0** (the base game, not the update).
  The [Cemu dumping guide](https://cemu.cfw.guide/dumping-games.html) covers dumping. A Cemu `.wua` archive works
  directly, no keys needed. A `.wux`/`.wud` image (with `GAME.key` and your `common.key`) or an extracted game
  folder also work.

What gets built contains the game, so it is for your own console only. Do not share it.

Tested on Windows 10 with SwitchWakerHD v0.2.0 and v0.3.0.

## 1. One-time setup

1. **Python 3** from [python.org](https://www.python.org/downloads/) (tick *Add python.exe to PATH*), then in a
   terminal:
   ```
   python -m pip install zstandard
   ```
   (`pycryptodome` too, only if you build from a `.wux`/`.wud`.)
2. **devkitPro**: download the Windows installer from <https://github.com/devkitPro/installer/releases> and install
   to the default `C:\devkitPro`. Keep at least the *Switch Development* component.
3. **Switch libraries**: open *devkitPro > MSYS2* from the Start menu (or `C:\devkitPro\msys2\msys2.exe`) and run:
   ```
   pacman -Syu
   pacman -S --needed switch-dev deko3d uam switch-lz4 switch-zlib switch-cmake
   ```
   `switch-dev` brings devkitA64 and libnx. If devkitPro is installed somewhere else, set the environment variable
   `DEVKITPRO_WIN` to that folder (with forward slashes, e.g. `D:/devkitPro`).

4. **Optional, for the HOME-screen icon** (see "HOME-screen icon" below): [Git](https://git-scm.com/download/win),
   `python -m pip install pillow`, and in the same devkitPro MSYS2 window `pacman -S gcc`.

You need about 10 GB of free disk space and 8 GB of RAM or more. Where the space goes (measured on a v0.3.0 build):

| | Size |
|---|---|
| devkitPro (the Switch toolchain) | 3 GB |
| Temporary build files (`build\switch-dk`, `build\gen`) | 3.4 GB |
| Your game, extracted (`build\sd-game`) | 1.7 GB |
| The finished SD card folder (`build\sd`) | 1.8 GB |

Only `build\sd` is needed after the build. Once it is on the SD card you can delete the rest of `build\` (about 5 GB).
Your `.wua` or other game dump (about 1.4 GB) is separate.

## 2. Get the release and add the script

1. Download the zip from <https://github.com/centollOS/SwitchWakerHD> (Releases) and unzip it.
   You get a folder like `SwitchWakerHD-v0.2.0` containing `README.md` and `tools\`.
2. Download `make_sd_windows.py` and `make_forwarder_windows.py` from this repo and **put both in that folder**,
   next to `README.md`. Nothing in the release is modified.

## 3. Build

In a terminal in that folder:

```
python make_sd_windows.py --wua "C:\path\to\The Legend of Zelda - The Wind Waker HD (US).wua"
```

Or, from another kind of dump:

```
python make_sd_windows.py --image C:\path\to\game.wux
python make_sd_windows.py --game-dir C:\path\to\10143500
```

On start the script hashes the release files it depends on (`setup.py`, `recomp.py`, `CMakeLists.txt`,
`build.sh`) and compares them with the release it was tested with (currently **v0.2.0** and **v0.3.0**). If they differ it prints
a warning and carries on, since the change may be harmless; `--strict` stops instead.

Options: `--jobs 2` if the PC runs out of memory (each compile can need ~1.5 GB), `--out DIR` for the output
folder, `--reuse-translation` to skip translating the game code again after a first run, `--strict` as above.

It extracts the game, checks it is the right version, translates the game code (a few minutes), then compiles
(10 to 30 minutes the first time). At the end, `build\sd\` holds:

```
build\sd\switch\wwhd\
├── wwhd.nro     the game
└── game\        your game's files
```

## 4. Play

Copy the **contents** of `build\sd\` to the root of the Switch's SD card, so the game ends up at
`sdmc:/switch/wwhd/wwhd.nro`. Hold **R** while starting any installed game to open the Homebrew Menu in title
mode, then pick SwitchWakerHD. Controls and settings are in the release's own `INSTALL.md`.

## HOME-screen icon (optional)

The release can also make a small forwarder, `wwhd_forwarder.nsp`, that puts a "Wind Waker HD" icon on the Switch
HOME screen and starts the game in title mode, so you do not have to hold R (the release's
`tools\switch\forwarder\INSTALL.md` describes it). `make_forwarder_windows.py` is the Windows, no-Docker version of
its build. `make_sd_windows.py` runs it **after the game has built**.

- It needs **your own console's `prod.keys`** (dump them with Lockpick_RCM). Put the file named `prod.keys` in the
  folder you run `make_sd_windows.py` from, or pass `--keys "D:\path\prod.keys"`. The keys are only read by path;
  they are never copied, and the output is checked so that no key value is printed. Keep the file out of anything
  you share: `.gitignore` here blocks `prod.keys`, `*.keys` and `*.nsp`.
- **Without keys it is skipped** with a short note, and the game (`wwhd.nro`) is built as usual. `--no-forwarder`
  skips it always.
- It needs Git, Pillow and `gcc` in MSYS2 (setup step 4). It fetches `nx-hbloader` and `hacBrewPack` from GitHub at
  fixed commits and compiles them.
- With keys, the `.nsp` is also copied to `build\sd\NSP\`. Install it on the Switch with DBI (*Browse SD card*, the
  `.nsp`, *Install*) or Goldleaf, and delete the file afterwards. It needs up-to-date Atmosphere sigpatches, and
  `wwhd.nro` must stay at `sdmc:/switch/wwhd/wwhd.nro`.
- Run it alone later with `python make_forwarder_windows.py --sd build\sd`.

Status: tested on a Switch by the author of this repo, with the game built by `make_sd_windows.py` from SwitchWakerHD
v0.3.0. The `.nsp` has not been checked offline with `hactool`. If something goes wrong on your console, please open
an issue with your firmware and Atmosphère versions.

If `hacBrewPack` says `Key (...) must be 32 hex digits`, a key in your `prod.keys` is not in the format it expects
(some key dumps have longer `master_kek_source_*` entries). Use keys from a current Lockpick_RCM dump.

## Troubleshooting

| | |
|---|---|
| `devkitPro is not set up ... (missing: ...)` | Redo step 1.2 and 1.3; the message lists what is missing |
| `put this file in the unzipped SwitchWakerHD release folder` | The script must sit next to the release's `tools\` folder |
| `not the expected file` / version error | The dump is not version 0 of the USA game, or the update was merged in |
| The compile fails with out-of-memory errors | Run again with `--jobs 2` |
| `skipped: no prod.keys at ...` | Normal without keys. See "HOME-screen icon" to add them |
| `WARNING: the forwarder was not built` | Only the icon failed; `wwhd.nro` and `build\sd` are fine. Read the lines above it |
| Something else | Run again and read the first error above `make_sd_windows: this step failed` |

## When the SwitchWakerHD release changes

If the warning above appears and the build fails, check this repo for a newer `make_sd_windows.py`. To add a new
release that works: build with it, run `python make_sd_windows.py --print-hashes` in its folder, and add the
printed lines as a new entry in the `TESTED` table at the top of the script.

## How it differs from the release's own build

Same steps and same output as `tools/switch/make_sd.py`; only the compile step differs. It runs devkitPro's own
MSYS2 `bash` (a plain `bash` on Windows may be WSL's) and uses CMake with *Unix Makefiles* instead of Ninja, with
compiler-generated dependency files switched off, because the Windows-native compiler writes `C:/` paths that
`make` cannot parse. It also creates the `dksh/` folder that the shader compiler writes into.

## License

MPL-2.0, like SwitchWakerHD (see `LICENSE`). The `.wua` reader is a Python port of `tools/wudextract/zarchive.cpp`
from the release.
