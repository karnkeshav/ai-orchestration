"""One-time setup for the Studio music engine (music_engine.py).

Installs, free of cost, into ./tools (git-ignored):
  * GeneralUser GS SoundFont (commercial use allowed, see tools/soundfonts/LICENSE.txt)
  * FluidSynth CLI (Windows only -- on Linux use: sudo apt install fluidsynth)

Also creates the music_library/<mood>/ folders for royalty-free tracks.

    python setup_music_tools.py
"""
import io
import json
import os
import platform
import shutil
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(ROOT, "tools")
SF_DIR = os.path.join(TOOLS, "soundfonts")
FS_DIR = os.path.join(TOOLS, "fluidsynth")

SF_URL = "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/GeneralUser-GS.sf2"
SF_LICENSE_URL = "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/documentation/LICENSE.txt"
FS_RELEASE_API = "https://api.github.com/repos/FluidSynth/fluidsynth/releases/latest"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ai-orchestration-setup"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def install_soundfont():
    os.makedirs(SF_DIR, exist_ok=True)
    target = os.path.join(SF_DIR, "GeneralUser-GS.sf2")
    if os.path.exists(target):
        print(f"✓ SoundFont already present: {target}")
        return
    print("⬇ Downloading GeneralUser GS SoundFont (~32 MB)...")
    data = _get(SF_URL)
    with open(target, "wb") as f:
        f.write(data)
    with open(os.path.join(SF_DIR, "LICENSE.txt"), "wb") as f:
        f.write(_get(SF_LICENSE_URL))
    print(f"✓ SoundFont saved: {target}")


def install_fluidsynth():
    if shutil.which("fluidsynth"):
        print(f"✓ FluidSynth on PATH: {shutil.which('fluidsynth')}")
        return
    if platform.system() != "Windows":
        print("ℹ FluidSynth not found. Install it with:  sudo apt install -y fluidsynth")
        print("  (until then the music engine falls back to its built-in synthesizer)")
        return
    exe = os.path.join(FS_DIR, "bin", "fluidsynth.exe")
    if os.path.exists(exe):
        print(f"✓ FluidSynth already present: {exe}")
        return
    release = json.loads(_get(FS_RELEASE_API))
    asset = next(a for a in release["assets"] if "win10-x64" in a["name"] and a["name"].endswith(".zip"))
    print(f"⬇ Downloading {asset['name']} ({release['tag_name']})...")
    with zipfile.ZipFile(io.BytesIO(_get(asset["browser_download_url"]))) as z:
        z.extractall(FS_DIR)
    # Some release zips nest everything in a top-level folder; flatten it.
    if not os.path.exists(exe):
        for dirpath, _, files in os.walk(FS_DIR):
            if "fluidsynth.exe" in files:
                src_root = os.path.dirname(dirpath)
                for item in os.listdir(src_root):
                    shutil.move(os.path.join(src_root, item), os.path.join(FS_DIR, item))
                os.rmdir(src_root)
                break
    print(f"✓ FluidSynth installed: {exe}")


INCOMPETECH_URL = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
# Kevin MacLeod (incompetech.com), CC BY 4.0 -- picked from incompetech's own genre/feel tags.
STARTER_TRACKS = {
    "emotional": ["Bittersweet", "Promises to Keep"],
    "happy": ["Carefree", "Wallpaper"],
    "festive": ["Vadodora", "Naraina"],
    "adventure": ["Heroic Age", "Prelude and Action"],
    "tech": ["Voxel Revolution", "Neon Laser Horizon", "Getting it Done"],
    "inspiring": ["Inspired", "Wholesome", "Heartwarming"],
    "mystery": ["Investigations", "Spy Glass"],
}


def create_library():
    from music_engine import MOODS, LIBRARY_DIR
    for mood in MOODS:
        os.makedirs(os.path.join(LIBRARY_DIR, mood), exist_ok=True)
    print(f"✓ Music library folders ready: {LIBRARY_DIR}/<mood> ({', '.join(MOODS)})")
    return LIBRARY_DIR


def fetch_starter_tracks(library_dir: str):
    """Download the CC BY starter tracks and write the required credit line next to each."""
    from urllib.parse import quote
    for mood, titles in STARTER_TRACKS.items():
        for title in titles:
            target = os.path.join(library_dir, mood, f"{title}.mp3")
            if os.path.exists(target):
                continue
            try:
                data = _get(INCOMPETECH_URL + quote(f"{title}.mp3"))
            except Exception as e:
                print(f"⚠ {title}: {e}")
                continue
            with open(target, "wb") as f:
                f.write(data)
            with open(os.path.join(library_dir, mood, f"{title}.credit.txt"), "w", encoding="utf-8") as f:
                f.write(f'"{title}" Kevin MacLeod (incompetech.com), licensed under Creative Commons: '
                        "By Attribution 4.0 License http://creativecommons.org/licenses/by/4.0/\n")
            print(f"✓ {mood}/{title}.mp3 ({len(data) // 1024} KB)")


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    install_soundfont()
    install_fluidsynth()
    lib = create_library()
    if "--no-tracks" not in sys.argv:
        fetch_starter_tracks(lib)
