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

Tested on Windows 10 with SwitchWakerHD v0.2.0.

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

You need about 10 GB of free disk space and 8 GB of RAM or more.

## 2. Get the release and add the script

1. Download the zip from <https://github.com/centollOS/SwitchWakerHD> (Releases) and unzip it.
   You get a folder like `SwitchWakerHD-v0.2.0` containing `README.md` and `tools\`.
2. Download `make_sd_windows.py` from this repo and **put it in that folder**, next to `README.md`.
   Nothing in the release is modified.

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

Options: `--jobs 2` if the PC runs out of memory (each compile can need ~1.5 GB), `--out DIR` for the output
folder, `--reuse-translation` to skip translating the game code again after a first run.

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

## Troubleshooting

| | |
|---|---|
| `devkitPro is not set up ... (missing: ...)` | Redo step 1.2 and 1.3; the message lists what is missing |
| `put this file in the unzipped SwitchWakerHD release folder` | The script must sit next to the release's `tools\` folder |
| `not the expected file` / version error | The dump is not version 0 of the USA game, or the update was merged in |
| The compile fails with out-of-memory errors | Run again with `--jobs 2` |
| Something else | Run again and read the first error above `make_sd_windows: this step failed` |

## How it differs from the release's own build

Same steps and same output as `tools/switch/make_sd.py`; only the compile step differs. It runs devkitPro's own
MSYS2 `bash` (a plain `bash` on Windows may be WSL's) and uses CMake with *Unix Makefiles* instead of Ninja, with
compiler-generated dependency files switched off, because the Windows-native compiler writes `C:/` paths that
`make` cannot parse. It also creates the `dksh/` folder that the shader compiler writes into.

## License

MPL-2.0, like SwitchWakerHD (see `LICENSE`). The `.wua` reader is a Python port of `tools/wudextract/zarchive.cpp`
from the release.
