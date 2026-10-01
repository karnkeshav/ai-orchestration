"""Free background music for Studio videos.

Three sources, all zero-cost (STUDIO_MUSIC_SOURCE=auto uses a library track for
STUDIO_MUSIC_LIBRARY_SHARE=0.5 of videos when the mood folder has tracks, otherwise midi -> synth):
  1. library -- royalty-free tracks you drop into music_library/<mood>/
                (Pixabay Music, YouTube Audio Library, Incompetech, ...).
                Put a credit line in "<track>.credit.txt" next to tracks that need attribution.
  2. midi    -- an original arrangement composed per prompt, written as MIDI and rendered
                with FluidSynth + the GeneralUser GS SoundFont (real piano/strings/sitar/synths).
  3. synth   -- the same arrangement rendered by a built-in numpy synthesizer
                (used when FluidSynth or the SoundFont is missing).

The mood is detected from the prompt (emotional, happy, festive, adventure, tech, inspiring,
mystery) -- IT/AI prompts map to "tech" (electronic pulse) or "inspiring" (uplifting piano).

build_soundtrack() returns a music bed of exactly the requested length, loudness-normalised,
with fades. mix_filter() returns the FFmpeg filter graph that ducks the music under the voice
and normalises the final mix to -14 LUFS (YouTube/Instagram loudness).

    python setup_music_tools.py                      # one-time: SoundFont + FluidSynth
    python music_engine.py "AI agents fix a cloud outage" --duration 15 --out test.wav
"""
import glob
import os
import random
import re
import shutil
import subprocess
import struct
import tempfile

import imageio_ffmpeg
import numpy as np
from scipy.io import wavfile
from scipy.signal import fftconvolve

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
ROOT = os.path.dirname(os.path.abspath(__file__))
LIBRARY_DIR = os.path.join(ROOT, "music_library")
SR = 44100
PPQ = 480
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
}

# Channels: 0 pad, 1 arpeggio, 2 bass, 3 melody, 9 drums. Programs are General MIDI (0-based).
MOODS = {
    "emotional": {
        "keywords": ["sad", "emotional", "farewell", "goodbye", "departing", "departure", "miss", "missing",
                     "cry", "tears", "alvida", "vidai", "vidaai", "mother", "maa", "papa", "father",
                     "brother", "sister", "didi", "bhai", "behan", "rakhi", "memories", "lonely", "heart",
                     "विदाई", "भाई", "बहन", "दीदी", "माँ", "याद"],
        "bpm": 72, "root": 57, "scale": "minor", "progression": [0, 5, 2, 6],
        "programs": {"pad": 48, "arp": 0, "bass": 32, "melody": 73},
        "arp": ([0, 2, 3, 4, 3, 2, 1, 2], 0.5), "bass": "whole", "drums": None,
        "rhythms": [[2, 1, 1], [1.5, 0.5, 2], [3, 1], [1, 1, 2]],
    },
    "happy": {
        "keywords": ["happy", "fun", "funny", "joy", "smile", "birthday", "friends", "friendship", "play",
                     "playful", "kids", "school", "picnic", "khushi", "masti", "dance", "cartoon"],
        "bpm": 108, "root": 60, "scale": "major", "progression": [0, 4, 5, 3],
        "programs": {"pad": 49, "arp": 0, "bass": 33, "melody": 12},
        "arp": ([0, 2, 3, 2, 1, 2, 3, 2], 0.5), "bass": "root5", "drums": "soft",
        "rhythms": [[1, 0.5, 0.5, 1, 1], [0.5, 0.5, 1, 0.5, 0.5, 1], [1, 1, 2], [0.5, 0.5, 0.5, 0.5, 2]],
    },
    "festive": {
        "keywords": ["diwali", "holi", "festival", "festive", "celebration", "celebrate", "wedding", "shaadi", "eid", "puja", "pooja",
                     "navratri", "garba", "ganesh", "dussehra", "lohri", "pongal", "onam", "baraat",
                     "दिवाली", "होली", "शादी", "त्योहार"],
        "bpm": 112, "root": 55, "scale": "major", "progression": [0, 3, 4, 0],
        "programs": {"pad": 48, "arp": 104, "bass": 32, "melody": 111},
        "arp": ([3, 2, 0, 2, 3, 4, 3, 2], 0.5), "bass": "syncop", "drums": "hand",
        "rhythms": [[0.5, 0.5, 1, 0.5, 0.5, 1], [1, 0.5, 0.5, 2], [0.5, 0.5, 0.5, 0.5, 1, 1]],
    },
    "adventure": {
        "keywords": ["adventure", "journey", "quest", "explore", "explorer", "magic", "magical", "dream",
                     "hero", "superhero", "space", "rocket", "fly", "flying", "dragon", "jungle", "treasure",
                     "disney", "pixar", "epic", "battle", "mission"],
        "bpm": 96, "root": 50, "scale": "minor", "progression": [0, 5, 3, 4],
        "programs": {"pad": 48, "arp": 45, "bass": 43, "melody": 60},
        "arp": ([0, 0, 2, 0, 3, 0, 2, 1], 0.5), "bass": "pulse8", "drums": "epic",
        "rhythms": [[1.5, 0.5, 2], [1, 1, 1, 1], [0.75, 0.25, 1, 2]],
    },
    "tech": {
        "keywords": ["ai", "a.i.", "artificial intelligence", "machine learning", "ml", "deep learning", "llm",
                     "gpt", "genai", "gen ai", "chatbot", "agent", "agents", "agentic", "neural", "model",
                     "cloud", "aws", "azure", "gcp", "oci", "kubernetes", "k8s", "docker", "devops", "mlops",
                     "finops", "terraform", "serverless", "data", "database", "analytics", "dashboard",
                     "code", "coding", "coder", "software", "developer", "programmer", "engineer", "server",
                     "api", "cyber", "cybersecurity", "hacker", "robot", "robotics", "automation", "automate",
                     "startup", "digital", "saas", "chip", "quantum", "blockchain", "tech", "technology",
                     "computer", "laptop", "app", "website", "network", "5g", "iot", "it department",
                     "it team", "it support", "it company", "it services", "it industry", "it job",
                     "it professional", "it sector"],
        "bpm": 120, "root": 52, "scale": "minor", "progression": [0, 5, 2, 6],
        "programs": {"pad": 89, "arp": 81, "bass": 38, "melody": 11},
        "arp": ([0, 1, 2, 3, 2, 1, 0, 1, 0, 1, 2, 3, 4, 3, 2, 1], 0.25), "bass": "pulse8", "drums": "four",
        "rhythms": [[0.75, 0.75, 0.5, 1, 1], [0.5, 0.5, 0.5, 0.5, 2], [1.5, 0.5, 2], [0.75, 0.75, 0.5, 2]],
    },
    "inspiring": {
        "keywords": ["success", "growth", "future", "innovation", "innovate", "transform", "transformation",
                     "launch", "vision", "career", "learn", "learning", "training", "course", "upskill",
                     "skill", "skills", "team", "teamwork", "leader", "leadership", "win", "achieve",
                     "motivation", "motivational", "inspire", "inspiring", "inspirational", "hope",
                     "promotion", "interview", "hired", "graduate", "students", "teacher", "mentor"],
        "bpm": 100, "root": 60, "scale": "major", "progression": [3, 0, 4, 5],
        "programs": {"pad": 48, "arp": 0, "bass": 32, "melody": 0},
        "arp": ([0, 2, 3, 2, 4, 2, 3, 2], 0.5), "bass": "half", "drums": "soft",
        "rhythms": [[1, 1, 2], [1.5, 0.5, 1, 1], [0.5, 0.5, 1, 2], [2, 2]],
    },
    "mystery": {
        "keywords": ["mystery", "mysterious", "detective", "secret", "suspense", "investigate",
                     "investigation", "crime", "clue", "bug", "outage", "incident", "breach", "attack",
                     "debug", "debugging", "dark", "night", "ghost", "haunted", "spy", "thriller", "solved"],
        "bpm": 84, "root": 50, "scale": "harmonic_minor", "progression": [0, 0, 5, 4],
        "programs": {"pad": 44, "arp": 10, "bass": 32, "melody": 8},
        "arp": ([0, 2, 1, 3], 1.0), "bass": "half", "drums": "pulse",
        "rhythms": [[2, 2], [1.5, 0.5, 2], [3, 1], [1, 1, 2]],
    },
}
MOOD_PRIORITY = ["festive", "mystery", "emotional", "tech", "inspiring", "adventure", "happy"]
DEFAULT_MOOD = "inspiring"


# --------------------------------------------------------------------------- mood

def detect_mood(prompt: str) -> str:
    """Pick the mood whose keywords occur most often in the prompt (ties -> MOOD_PRIORITY)."""
    text = (prompt or "").lower()
    scores = {}
    for mood, cfg in MOODS.items():
        n = 0
        for kw in cfg["keywords"]:
            if kw.isascii():
                n += len(re.findall(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text))
            else:  # Devanagari: \b is unreliable around combining vowel signs
                n += text.count(kw)
        scores[mood] = n
    best = max(scores.values())
    if best == 0:
        return DEFAULT_MOOD
    return next(m for m in MOOD_PRIORITY if scores[m] == best)


# --------------------------------------------------------------------------- composition

def _scale_note(cfg, degree: int) -> int:
    scale = SCALES[cfg["scale"]]
    octave, idx = divmod(degree, 7)
    return cfg["root"] + 12 * octave + scale[idx]


def _chord_degrees(base: int):
    return [base, base + 2, base + 4]


def compose(mood: str, duration_sec: float, seed: int):
    """Return (notes, bpm) where notes = [(start_beat, dur_beats, channel, pitch, velocity)]."""
    cfg = MOODS[mood]
    rng = random.Random(seed)
    bpm = cfg["bpm"]
    total_bars = int(np.ceil(duration_sec * bpm / 60.0 / 4.0)) + 1
    intro = 1 if total_bars <= 6 else 2
    prog = cfg["progression"]
    notes = []

    def add(start, dur, ch, pitch, vel):
        vel = max(1, min(127, int(vel + rng.randint(-5, 5))))
        notes.append((start, dur, ch, int(pitch), vel))

    # Two melodic phrases (rhythm + contour) reused across the piece so it sounds composed.
    def make_phrase():
        bars = [rng.choice(cfg["rhythms"]) for _ in range(2)]
        steps = [rng.choice([-2, -1, -1, 0, 1, 1, 2, 3, -3]) for _ in range(sum(len(b) for b in bars))]
        return bars, steps
    phrases = [make_phrase(), make_phrase()]
    phrase_order = [0, 0, 1, 0]
    mel_deg = 9  # melody walks in scale degrees, roughly one octave above the root

    for bar in range(total_bars):
        b0 = bar * 4.0
        last = bar == total_bars - 1
        chord_base = 0 if last else prog[bar % len(prog)]
        chord = _chord_degrees(chord_base)
        chord_pitches = [_scale_note(cfg, d) for d in chord]

        # Pad: sustained triad around the root.
        for p in chord_pitches:
            add(b0, 4.0, 0, p, 58)

        # Bass.
        root_b = _scale_note(cfg, chord_base) - 24
        fifth_b = _scale_note(cfg, chord_base + 4) - 24
        style = "whole" if last else cfg["bass"]
        if style == "whole":
            add(b0, 4.0, 2, root_b, 85)
        elif style == "half":
            add(b0, 2.0, 2, root_b, 85); add(b0 + 2, 2.0, 2, root_b, 78)
        elif style == "root5":
            for i, p in enumerate([root_b, fifth_b, root_b, fifth_b]):
                add(b0 + i, 0.9, 2, p, 85 if i % 2 == 0 else 72)
        elif style == "pulse8":
            for i in range(8):
                add(b0 + i * 0.5, 0.45, 2, root_b, 88 if i % 2 == 0 else 70)
        elif style == "syncop":
            for off, d in [(0, 1.5), (1.5, 0.5), (2, 1.5), (3.5, 0.5)]:
                add(b0 + off, d * 0.9, 2, root_b if off != 3.5 else fifth_b, 85)

        # Arpeggio over the chord (+ octave above).
        tones = chord_pitches + [p + 12 for p in chord_pitches]
        pattern, step = cfg["arp"]
        if last:
            add(b0, 4.0, 1, tones[0] + 12, 70)
        else:
            t = 0.0
            i = 0
            while t < 4.0 - 1e-6:
                add(b0 + t, step * 0.9, 1, tones[pattern[i % len(pattern)] % len(tones)], 72 if i % 2 == 0 else 60)
                t += step
                i += 1

        # Melody (after the intro).
        if bar >= intro and not last:
            phrase_bar = (bar - intro) % 8
            bars_rhythm, steps = phrases[phrase_order[phrase_bar // 2]]
            rhythm = bars_rhythm[phrase_bar % 2]
            step_offset = 0 if phrase_bar % 2 == 0 else len(bars_rhythm[0])
            chord_set = {d % 7 for d in chord}
            t = 0.0
            for j, dur in enumerate(rhythm):
                mel_deg += steps[(step_offset + j) % len(steps)]
                mel_deg = max(7, min(16, mel_deg))
                if t in (0.0, 2.0):  # strong beats land on a chord tone
                    cands = [d for d in range(7, 17) if d % 7 in chord_set]
                    mel_deg = min(cands, key=lambda d: abs(d - mel_deg))
                add(b0 + t, dur * 0.95, 3, _scale_note(cfg, mel_deg), 88 if t in (0.0, 2.0) else 76)
                t += dur
        elif last:
            add(b0, 4.0, 3, _scale_note(cfg, 7), 80)

        # Drums (channel 9) in the full section.
        if cfg["drums"] and bar >= intro:
            for off, note, vel in _drum_bar(cfg["drums"], first=bar == intro, last=last):
                add(b0 + off, 0.25, 9, note, vel)

    return notes, bpm


def _drum_bar(style: str, first: bool, last: bool):
    hits = []
    if first or last:
        hits.append((0, 49, 90))  # crash
    if last:
        return hits + [(0, 36, 100)]
    if style == "soft":
        hits += [(0, 36, 80), (2, 36, 75), (1, 37, 60), (3, 37, 60)]
        hits += [(i * 0.5, 42, 50 if i % 2 == 0 else 38) for i in range(8)]
    elif style == "four":
        hits += [(i, 36, 95) for i in range(4)] + [(1, 39, 80), (3, 39, 80)]
        hits += [(i * 0.25, 42, 55 if i % 2 == 0 else 32) for i in range(16)]
        hits += [(i + 0.5, 46, 45) for i in range(4)]
    elif style == "hand":
        hits += [(0, 64, 90), (1.5, 64, 80), (2.5, 64, 85), (0, 36, 60), (2, 36, 55)]
        hits += [(o, 60, 75) for o in (0.5, 1, 2, 3, 3.5)] + [(o, 62, 65) for o in (0.75, 2.75)]
        hits += [(i * 0.5, 54, 35) for i in range(8)]
    elif style == "epic":
        hits += [(0, 36, 100), (2.5, 36, 85), (3, 38, 75), (2, 41, 80), (3.5, 45, 70)]
    elif style == "pulse":
        hits += [(0, 36, 70), (1.75, 45, 45)] + [(i * 0.5, 70, 25) for i in range(8)]
    return hits


# --------------------------------------------------------------------------- MIDI writer

def _varlen(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def write_midi(notes, bpm: int, programs: dict, path: str):
    """Write a format-0 Standard MIDI File (no external dependency)."""
    ch_prog = {0: programs["pad"], 1: programs["arp"], 2: programs["bass"], 3: programs["melody"]}
    ch_mix = {0: (78, 64, 70), 1: (68, 44, 50), 2: (92, 64, 10), 3: (96, 84, 60), 9: (85, 64, 30)}
    events = []  # (tick, order, bytes) -- order puts note-offs before note-ons on the same tick
    tempo = int(60_000_000 / bpm)
    events.append((0, 0, b"\xFF\x51\x03" + tempo.to_bytes(3, "big")))
    for ch, prog in ch_prog.items():
        events.append((0, 0, bytes([0xC0 | ch, prog])))
    for ch, (vol, pan, rev) in ch_mix.items():
        events += [(0, 0, bytes([0xB0 | ch, 7, vol])), (0, 0, bytes([0xB0 | ch, 10, pan])),
                   (0, 0, bytes([0xB0 | ch, 91, rev]))]
    for start, dur, ch, pitch, vel in notes:
        on = int(round(start * PPQ))
        off = max(on + 1, int(round((start + dur) * PPQ)))
        events.append((on, 2, bytes([0x90 | ch, pitch, vel])))
        events.append((off, 1, bytes([0x80 | ch, pitch, 0])))
    events.sort(key=lambda e: (e[0], e[1]))
    track = bytearray()
    last = 0
    for tick, _, data in events:
        track += _varlen(tick - last) + data
        last = tick
    track += _varlen(PPQ) + b"\xFF\x2F\x00"
    with open(path, "wb") as f:
        f.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ))
        f.write(b"MTrk" + struct.pack(">I", len(track)) + bytes(track))
    return path


# --------------------------------------------------------------------------- renderers

def find_fluidsynth():
    exe = os.environ.get("FLUIDSYNTH_PATH")
    if exe and os.path.exists(exe):
        return exe
    for cand in (os.path.join(ROOT, "tools", "fluidsynth", "bin", "fluidsynth.exe"),
                 os.path.join(ROOT, "tools", "fluidsynth", "bin", "fluidsynth")):
        if os.path.exists(cand):
            return cand
    return shutil.which("fluidsynth")


def find_soundfont():
    sf = os.environ.get("SOUNDFONT_PATH")
    if sf and os.path.exists(sf):
        return sf
    cands = sorted(glob.glob(os.path.join(ROOT, "tools", "soundfonts", "*.sf2")))
    cands += ["/usr/share/sounds/sf2/FluidR3_GM.sf2", "/usr/share/soundfonts/FluidR3_GM.sf2",
              "/usr/share/soundfonts/default.sf2", "/usr/share/sounds/sf2/default-GM.sf2"]
    return next((c for c in cands if os.path.exists(c)), None)


def render_fluidsynth(midi_path: str, wav_path: str) -> bool:
    exe, sf = find_fluidsynth(), find_soundfont()
    if not exe or not sf:
        return False
    cmd = [exe, "-ni", "-q", "-g", "0.6", "-r", str(SR), "-F", wav_path, "-T", "wav", sf, midi_path]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 1000


def _tone(kind: str, freq: float, n: int, vel: float):
    t = np.arange(n) / SR
    if kind == "pluck":
        w = sum(a * np.sin(2 * np.pi * freq * h * t) for h, a in [(1, 1), (2, 0.45), (3, 0.2), (4, 0.1)])
        env = np.exp(-t * 3.2)
    elif kind == "bell":
        w = np.sin(2 * np.pi * freq * t) + 0.4 * np.sin(2 * np.pi * freq * 2.76 * t) + 0.2 * np.sin(2 * np.pi * freq * 5.4 * t)
        env = np.exp(-t * 2.5)
    elif kind == "bass":
        w = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * t)
        env = np.exp(-t * 1.5)
    elif kind == "pad":
        w = sum(np.sin(2 * np.pi * freq * d * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * d * t)
                for d in (0.997, 1.0, 1.003)) / 3
        env = np.minimum(1.0, t / 0.35)
    elif kind == "saw":
        w = sum(np.sin(2 * np.pi * freq * h * t) / h for h in range(1, 8))
        env = np.exp(-t * 4.0)
    else:  # "lead": flute / horn / shehnai -- soft odd harmonics with vibrato
        vib = 1 + 0.004 * np.sin(2 * np.pi * 5.2 * t) * np.minimum(1.0, t / 0.4)
        ph = 2 * np.cumsum(np.pi * freq * vib) / SR
        w = np.sin(ph) + 0.25 * np.sin(3 * ph) + 0.1 * np.sin(5 * ph)
        env = np.minimum(1.0, t / 0.06)
    rel = min(n, int(0.08 * SR))
    env[-rel:] *= np.linspace(1, 0, rel)
    return w * env * (vel / 127.0)


def _drum(note: int, vel: float):
    n = int(0.4 * SR)
    t = np.arange(n) / SR
    noise = np.random.default_rng(note).uniform(-1, 1, n)
    if note in (35, 36):
        w = np.sin(2 * np.pi * (50 + 90 * np.exp(-t * 30)) * t) * np.exp(-t * 9)
    elif note in (41, 43, 45, 47, 48, 50, 60, 61, 62, 63, 64):
        f = {41: 90, 43: 110, 45: 130, 47: 160, 48: 190, 50: 220, 60: 420, 61: 330, 62: 300, 63: 260, 64: 180}[note]
        w = np.sin(2 * np.pi * f * t) * np.exp(-t * 14) + 0.1 * noise * np.exp(-t * 40)
    elif note in (42, 44, 46, 54, 70):
        decay = 9 if note == 46 else 45
        w = np.diff(noise, prepend=0) * np.exp(-t * decay) * 0.5
    elif note == 49:
        w = np.diff(noise, prepend=0) * np.exp(-t * 4) * 0.5
    else:  # snare / clap / side stick
        w = noise * np.exp(-t * 22) * 0.7 + np.sin(2 * np.pi * 190 * t) * np.exp(-t * 25) * 0.3
    return w * (vel / 127.0)


def _kind(program: int) -> str:
    if 32 <= program <= 39:
        return "bass"
    if 40 <= program <= 44 or 48 <= program <= 55 or 88 <= program <= 95:
        return "pad"
    if 8 <= program <= 15 or 96 <= program <= 103:
        return "bell"
    if 80 <= program <= 87:
        return "saw"
    if program <= 7 or 24 <= program <= 31 or program in (45, 46, 104, 105, 106, 107):
        return "pluck"
    return "lead"


def render_numpy(notes, bpm: int, programs: dict, wav_path: str, duration_sec: float):
    """Fallback renderer: additive synthesis + algorithmic reverb, no external tools."""
    spb = 60.0 / bpm
    length = int((duration_sec + 3.0) * SR)
    out = np.zeros((length, 2))
    ch_prog = {0: programs["pad"], 1: programs["arp"], 2: programs["bass"], 3: programs["melody"]}
    ch_gain = {0: 0.18, 1: 0.22, 2: 0.45, 3: 0.35, 9: 0.5}
    ch_pan = {0: 0.5, 1: 0.3, 2: 0.5, 3: 0.65, 9: 0.5}
    for start, dur, ch, pitch, vel in notes:
        s = int(start * spb * SR)
        if s >= length:
            continue
        if ch == 9:
            w = _drum(pitch, vel)
        else:
            kind = _kind(ch_prog[ch])
            n = int((dur * spb + (0.25 if kind in ("pluck", "bell") else 0.05)) * SR)
            w = _tone(kind, 440.0 * 2 ** ((pitch - 69) / 12), n, vel)
        w = w[: length - s] * ch_gain[ch]
        pan = ch_pan[ch]
        out[s:s + len(w), 0] += w * np.sqrt(1 - pan)
        out[s:s + len(w), 1] += w * np.sqrt(pan)
    ir_t = np.arange(int(1.8 * SR)) / SR
    rng = np.random.default_rng(7)
    for c in range(2):
        ir = rng.uniform(-1, 1, len(ir_t)) * np.exp(-ir_t * 3.5)
        wet = fftconvolve(out[:, c], ir)[:length]
        out[:, c] = out[:, c] + 0.06 * wet / (np.max(np.abs(wet)) + 1e-9) * np.max(np.abs(out[:, c]))
    out /= np.max(np.abs(out)) + 1e-9
    wavfile.write(wav_path, SR, (out * 0.9 * 32767).astype(np.int16))
    return wav_path


# --------------------------------------------------------------------------- library

def library_tracks(mood: str):
    folder = os.path.join(LIBRARY_DIR, mood)
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(AUDIO_EXTS))


def _track_credit(track: str):
    sidecar = os.path.splitext(track)[0] + ".credit.txt"
    if os.path.exists(sidecar):
        with open(sidecar, encoding="utf-8") as f:
            return f.read().strip() or None
    return None


# --------------------------------------------------------------------------- public API

def _finalize(src: str, out_wav: str, duration_sec: float, loop: bool = False):
    """Trim/loop to length, fade in/out, normalise the music bed to -20 LUFS."""
    fade_out = min(2.0, duration_sec / 4)
    af = (f"afade=t=in:d=0.8,afade=t=out:st={duration_sec - fade_out:.2f}:d={fade_out:.2f},"
          f"loudnorm=I=-20:TP=-2:LRA=11,aresample={SR}")
    cmd = [FFMPEG_EXE, "-y"] + (["-stream_loop", "-1"] if loop else []) + [
        "-i", src, "-t", f"{duration_sec:.2f}", "-af", af, "-ac", "2", "-ar", str(SR), out_wav]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return out_wav


def build_soundtrack(prompt: str, duration_sec: float, out_wav: str, mood: str = None,
                     source: str = None, seed: int = None) -> dict:
    """Create a music bed for `prompt`. Returns {path, mood, source, credit, seed, track}."""
    mood = mood if mood in MOODS else detect_mood(prompt)
    source = (source or os.environ.get("STUDIO_MUSIC_SOURCE", "auto")).lower()
    seed = seed if seed is not None else random.SystemRandom().randrange(1 << 30)
    info = {"path": out_wav, "mood": mood, "source": None, "credit": None, "seed": seed, "track": None}

    if source in ("auto", "library"):
        tracks = library_tracks(mood)
        share = float(os.environ.get("STUDIO_MUSIC_LIBRARY_SHARE", "0.5"))
        if tracks and (source == "library" or random.Random(seed).random() < share):
            track = random.Random(seed + 1).choice(tracks)
            _finalize(track, out_wav, duration_sec, loop=True)
            info.update(source="library", track=os.path.basename(track), credit=_track_credit(track))
            return info

    with tempfile.TemporaryDirectory() as tmp:
        notes, bpm = compose(mood, duration_sec, seed)
        programs = MOODS[mood]["programs"]
        raw = os.path.join(tmp, "raw.wav")
        rendered = False
        if source in ("auto", "library", "midi"):
            midi = write_midi(notes, bpm, programs, os.path.join(tmp, "music.mid"))
            rendered = render_fluidsynth(midi, raw)
            if rendered:
                info["source"] = "midi"
        if not rendered:
            render_numpy(notes, bpm, programs, raw, duration_sec)
            info["source"] = "synth"
        _finalize(raw, out_wav, duration_sec)
    return info


def mix_filter(voice_input: int, music_input: int, voice_delay: float, total_sec: float) -> str:
    """FFmpeg filter_complex fragment that outputs [a]: voice delayed by `voice_delay`, music
    ducked under the voice (sidechain compression), final mix normalised to -14 LUFS."""
    d = int(voice_delay * 1000)
    fmt = f"aformat=sample_fmts=fltp:sample_rates={SR}:channel_layouts=stereo"
    return (
        f"[{voice_input}:a]{fmt},adelay={d}|{d},apad,atrim=0:{total_sec:.2f},asplit=2[vmix][vkey];"
        f"[{music_input}:a]{fmt},volume=0.8[mus];"
        f"[mus][vkey]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=450[duck];"
        f"[vmix][duck]amix=inputs=2:duration=first:normalize=0,"
        f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample={SR}[a]"
    )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Render a free background music bed for a prompt.")
    ap.add_argument("prompt")
    ap.add_argument("--duration", type=float, default=15.0)
    ap.add_argument("--out", default="music_test.wav")
    ap.add_argument("--mood", choices=list(MOODS))
    ap.add_argument("--source", choices=["auto", "library", "midi", "synth"])
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    print(build_soundtrack(a.prompt, a.duration, a.out, mood=a.mood, source=a.source, seed=a.seed))
