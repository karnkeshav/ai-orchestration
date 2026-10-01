"""Hollywood-style multi-scene promo videos from one prompt.

The Pixar engine (hybrid_video_engine.py) makes a single-image story video. This engine makes a
trailer-style film: one AI image per scene, a camera move per scene (push-in, pull-back, pan,
crane, Dutch tilt, crash zoom), transitions between scenes, act tags, title cards, burned-in
subtitles, a deep voiceover fitted to the requested length, a ducked music bed and whoosh / boom
sound effects. The studio outro and end card are appended (shape picked by the video's shape).

Scene plan, in order of preference:
  1. a shot list in the prompt (SHOT / SCENE / PART blocks with Camera:, Visual:, VO:, Title card:,
     SFX:, Transition: lines) -- followed as written;
  2. Gemini (free tier) writes a scene plan from a plain brief;
  3. an offline problem -> boom -> solution -> benefits -> call-to-action template.

    python hollywood_video_engine.py brief.txt --out promo.mp4
"""
import asyncio
import concurrent.futures
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.io import wavfile

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
SR = 44100
FPS = 25
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
DEFAULT_STYLE = ("cinematic film still, teal and orange colour grade, anamorphic lens flare, "
                 "volumetric light and haze, shallow depth of field, dramatic lighting, high detail")

# ----------------------------------------------------------------------------- prompt parsing

_LANDSCAPE_RE = re.compile(r"16\s*[:x/]\s*9|1920\s*x\s*1080|\b(?:landscape|widescreen|youtube video|horizontal)\b", re.I)
_VERTICAL_RE = re.compile(r"9\s*[:x/]\s*16|1080\s*x\s*1920|\b(?:vertical|portrait|reels?|shorts|instagram)\b", re.I)


def aspect_from_prompt(prompt: str, default: str = "16:9") -> str:
    """The first shape the prompt names (a brief states its own format before e.g. a 9:16 outro)."""
    land, vert = _LANDSCAPE_RE.search(prompt or ""), _VERTICAL_RE.search(prompt or "")
    if land and (not vert or land.start() < vert.start()):
        return "16:9"
    return "9:16" if vert else default


def duration_from_prompt(prompt: str, default: float = 60.0) -> float:
    p = (prompt or "").lower()
    m = re.search(r"\b(\d{1,3})\s*-?\s*(?:seconds?|secs?|s)\b", p)
    if m and 10 <= int(m.group(1)) <= 180:
        return float(m.group(1))
    m = re.search(r"\b(\d)\s*-?\s*min(?:ute)?s?\b", p)
    if m and 1 <= int(m.group(1)) <= 3:
        return 60.0 * int(m.group(1))
    if re.search(r"\b(?:one|a)\s*-?\s*minute\b", p):
        return 60.0
    return default


_SHOT_RE = re.compile(r"^\s*(?:SHOT|SCENE)\s*(\d+)\b(.*)$", re.I)
_PART_RE = re.compile(r"^\s*=*\s*(?:PART|ACT)\s*(\d+)\s*[:\-]?\s*(.*?)\s*=*\s*$", re.I)
_STOP_RE = re.compile(r"^\s*=*\s*(?:FINISH|RULES|GLOBAL STYLE|STYLE)\b", re.I)
_FIELD_RE = re.compile(r"^\s*(?:[-*]\s+)?([A-Za-z][A-Za-z /\-]{0,24}?)\s*(?:\([^)]*\))?\s*:\s*(.*)$")
_TIME_RE = re.compile(r"\((\d+):(\d+(?:\.\d+)?)\s*-\s*(\d+):(\d+(?:\.\d+)?)\)|\((\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*s\)")
_TEXT_KEYS = ("title card", "title card overlay", "title", "text", "on-screen text", "big text",
              "text overlay", "tagline")
_FIELD_KEYS = {"camera", "visual", "sfx", "vo", "voiceover", "voice over", "transition", "small text",
               "small line", "look", "stations"} | set(_TEXT_KEYS)


def _quoted(value: str):
    return re.findall(r'"([^"]{1,200})"', value or "")


def _span(m):
    if not m:
        return None
    if m.group(1) is not None:
        a = int(m.group(1)) * 60 + float(m.group(2))
        b = int(m.group(3)) * 60 + float(m.group(4))
    else:
        a, b = float(m.group(5)), float(m.group(6))
    return b - a if b > a else None


def parse_shot_list(prompt: str):
    """-> (scenes, style) when the prompt is a shot list, else (None, style)."""
    lines = (prompt or "").splitlines()
    use_parts = not any(_SHOT_RE.match(l) for l in lines)
    style, act, blocks, cur, key = None, "", [], None, None
    for raw in lines:
        line = raw.rstrip()
        if _STOP_RE.match(line):
            cur, key = None, None
            continue
        pm = _PART_RE.match(line)
        if pm:
            name = re.sub(r"\(.*?\)", "", pm.group(2).split(" - ")[0]).strip(" =-:").upper()
            act = f"ACT {pm.group(1)}: {name}" if name else f"ACT {pm.group(1)}"
            if use_parts:
                cur = {"act": act, "fields": {}, "dur": _span(_TIME_RE.search(line))}
                blocks.append(cur)
                key = None
            continue
        sm = None if use_parts else _SHOT_RE.match(line)
        if sm:
            cur = {"act": act, "fields": {}, "dur": _span(_TIME_RE.search(line))}
            blocks.append(cur)
            key = None
            continue
        fm = _FIELD_RE.match(line)
        if fm and fm.group(1).strip().lower() in _FIELD_KEYS:
            k = fm.group(1).strip().lower()
            if k == "look" and cur is None:
                style, key = fm.group(2).strip(), "look"
                continue
            if cur is not None:
                key = k
                cur["fields"][k] = (cur["fields"].get(k, "") + " " + fm.group(2)).strip()
            continue
        if cur is None and key == "look" and line.strip() and not line.lstrip().startswith("-"):
            style += " " + line.strip()
        elif cur is None:
            key = None
        elif key and line.strip():
            cur["fields"][key] += " " + line.strip()
    scenes = []
    for b in blocks:
        f = b["fields"]
        vo_raw = f.get("vo") or f.get("voiceover") or f.get("voice over") or ""
        vo = " ".join(_quoted(vo_raw)) or ("" if vo_raw.strip().startswith("(") else vo_raw.strip())
        texts = []
        for k in _TEXT_KEYS:  # title card(s) first, tagline as the next line
            if f.get(k):
                q = _quoted(f[k])
                texts += q if q else [f[k].strip()]
        if not (vo or f.get("visual") or f.get("camera") or texts):
            continue
        visual = " ".join(x for x in (f.get("visual", ""), f.get("camera", "")) if x) or " ".join(texts) or vo
        scenes.append({
            "act": b["act"], "narration": vo, "title_text": "\n".join(texts[:2])[:110],
            "small_text": " ".join(_quoted(f.get("small text", "") + f.get("small line", "")))[:80],
            "visual": visual, "camera": f.get("camera", ""),
            "sfx": f.get("sfx", ""), "transition": f.get("transition", ""), "dur": b["dur"],
        })
    return (split_long_scenes(scenes) if len(scenes) >= 3 else None), (style or None)


_ANGLES = ("wide establishing view", "dramatic close view", "view from above", "low-angle hero view")


def split_long_scenes(scenes, max_len: float = 7.0, part_len: float = 5.0):
    """A long shot (e.g. a 15 s montage) becomes several images with their own camera moves; the
    narration's sentences are shared across the parts so each part speaks over its own image."""
    out = []
    for s in scenes:
        d = s.get("dur") or 0
        if d <= max_len:
            out.append(s)
            continue
        n = max(2, int(round(d / part_len)))
        sentences = re.split(r"(?<=[.!?])\s+", s.get("narration", "").strip()) if s.get("narration") else []
        per = max(1, -(-len(sentences) // n)) if sentences else 0
        group = len(out)  # the parts share the first part's generated image (new angle each)
        for k in range(n):
            part = dict(s, dur=d / n, visual=f"{s['visual']}, {_ANGLES[k % len(_ANGLES)]}", img_group=group,
                        camera=("tracking fpv flight", "push-in", "crane rising", "pan")[k % 4] + " " + s.get("camera", ""),
                        narration=" ".join(sentences[k * per:(k + 1) * per]) if sentences else "",
                        title_text=s["title_text"] if k == n - 1 else "",
                        transition="whip" if k < n - 1 else s.get("transition", ""),
                        sfx="whoosh" if k else s.get("sfx", ""))
            out.append(part)
    return out


GEMINI_MODELS = os.environ.get("GEMINI_SCRIPT_MODELS",
                               "gemini-3.6-flash,gemini-flash-lite-latest,gemini-2.5-flash").split(",")


def gemini_scene_plan(prompt: str, target: float):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    from google import genai
    from google.genai import types
    words = int(target * 2.4)
    instruction = (
        "You are a Hollywood trailer editor. Turn this video brief into a fast-paced trailer-style "
        f"promo of about {int(target)} seconds.\nBrief: {prompt}\n"
        "Structure: a sharp problem statement, a dramatic 'boom' reveal of the solution, how it works, "
        "the benefits, and a call to action. Use only facts present in the brief; invent no numbers.\n"
        "Return JSON: {\"scenes\": [ ... 7 to 10 objects ... ]} where each object has:\n"
        '  "act": short act label in capitals (e.g. "ACT I: THE PROBLEM")\n'
        '  "visual": a vivid image prompt for this scene (subject, setting, lighting), no text in the image\n'
        '  "camera": one of push-in, pull-back, pan-left, pan-right, crane-up, dutch, crash-zoom, orbit\n'
        '  "title_text": big on-screen title card, max 40 characters\n'
        f'  "narration": the voiceover line for this scene (all scenes together about {words} words)\n'
        '  "sfx": one of whoosh, boom, riser, chime, none\n'
        '  "transition": one of fade, dip-to-black, white-flash, whip, glitch, zoom, cut\n'
        '  "seconds": scene length in seconds (all scenes together about the target length)'
    )
    client = genai.Client(api_key=api_key)
    for model in GEMINI_MODELS:
        try:
            resp = client.models.generate_content(
                model=model, contents=instruction,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.8))
            data = json.loads(resp.text)
            raw = data.get("scenes") if isinstance(data, dict) else data
            scenes = []
            for s in raw or []:
                if not isinstance(s, dict) or not (s.get("visual") or s.get("narration")):
                    continue
                scenes.append({"act": str(s.get("act", ""))[:40], "visual": str(s.get("visual", "")),
                               "camera": str(s.get("camera", "")), "title_text": str(s.get("title_text", ""))[:60],
                               "small_text": "", "narration": str(s.get("narration", "")),
                               "sfx": str(s.get("sfx", "")), "transition": str(s.get("transition", "")),
                               "dur": float(s["seconds"]) if str(s.get("seconds", "")).replace(".", "", 1).isdigit() else None})
            if len(scenes) >= 4:
                return scenes
        except Exception as e:
            print(f"[hollywood] Gemini {model} failed: {type(e).__name__} {str(e)[:120]}")
    return None


def template_scene_plan(prompt: str, target: float):
    topic = re.sub(r"\s+", " ", prompt or "").strip()
    m = re.search(r"\b(?:about|for|on|of)\s+(.{8,90}?)(?:[.,;]|$)", topic, re.I)
    topic = (m.group(1) if m else topic[:80]).strip()
    S = lambda act, visual, camera, title, vo, sfx, tr: {
        "act": act, "visual": visual, "camera": camera, "title_text": title, "small_text": "",
        "narration": vo, "sfx": sfx, "transition": tr, "dur": None}
    return [
        S("ACT I: THE PROBLEM", "a lone engineer at night surrounded by glowing screens of errors and alerts, city lights behind",
          "push-in", "THE OLD WAY IS BROKEN", f"Every team knows the pain. {topic}, done the old way, takes hours of manual work.", "whoosh", "dip-to-black"),
        S("ACT I: THE PROBLEM", "red warning holograms and a ticking clock over a cluttered desk, cold blue light",
          "dutch", "HOURS. MISTAKES. COST.", "Endless steps. Missed details. Surprise costs.", "boom", "cut"),
        S("ACT II: THE BREAKTHROUGH", "an explosion of neon light forming a glowing AI core in a dark void",
          "crash-zoom", "UNTIL NOW", "Until now.", "boom", "white-flash"),
        S("ACT II: THE BREAKTHROUGH", "a giant floating holographic prompt box glowing cyan in a dark futuristic studio",
          "push-in", "ONE PROMPT", "With AI Orchestration Studio, one prompt does it all.", "riser", "zoom"),
        S("ACT III: THE SOLUTION", "a high-speed flight through a glowing data pipeline tunnel of light gates",
          "pan-right", "AUTOMATED END TO END", f"It plans, builds and checks everything for {topic}, automatically.", "whoosh", "whip"),
        S("ACT IV: THE BENEFITS", "a triumphant team in a bright modern office looking at a glowing success dashboard",
          "crane-up", "HOURS BECOME MINUTES", "Hours become minutes. Fewer mistakes. Full control.", "chime", "fade"),
        S("ACT V: THE TITLE", "a chrome logo glowing in volumetric light with dust particles and lens flare",
          "pull-back", "AI ORCHESTRATION STUDIO", "AI Orchestration Studio. One prompt. Real results.", "boom", "fade"),
    ]


# ----------------------------------------------------------------------------- media helpers

def _run(cmd):
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    if res.returncode != 0:
        raise RuntimeError(res.stderr[-900:])
    return res


def _duration(path: str) -> float:
    out = subprocess.run([FFMPEG_EXE, "-i", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, errors="replace").stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def fetch_scene_image(prompt: str, size, dest: str, seed: int) -> bool:
    """Free AI still from Pollinations (no key). False on any failure."""
    w, h = size
    q = urllib.parse.quote(prompt[:900])
    url = f"https://image.pollinations.ai/prompt/{q}?width={w}&height={h}&nologo=true&model=flux&seed={seed}"
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ai-orchestration-studio"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            if len(data) > 20000:
                with open(dest, "wb") as f:
                    f.write(data)
                Image.open(dest).verify()
                return True
        except Exception as e:
            print(f"[hollywood] image failed: {type(e).__name__} {str(e)[:100]}")
    return False


def fallback_scene_image(dest: str, size, idx: int, title: str):
    """Designed backdrop when no AI image is available: graded gradient, light orbs, rays."""
    W, H = size
    rnd = random.Random(idx)
    top = (rnd.randint(5, 25), rnd.randint(10, 35), rnd.randint(40, 80))
    bot = (rnd.randint(40, 90), rnd.randint(10, 40), rnd.randint(60, 120))
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    for y in range(H):
        r = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] * (1 - r) + bot[i] * r) for i in range(3)))
    glow = Image.new("RGB", size)
    gd = ImageDraw.Draw(glow)
    for _ in range(5):
        cx, cy, R = rnd.randint(0, W), rnd.randint(0, H), rnd.randint(min(W, H) // 6, min(W, H) // 2)
        gd.ellipse([cx - R, cy - R, cx + R, cy + R], fill=rnd.choice([(0, 200, 255), (170, 80, 255), (255, 150, 60)]))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(min(W, H) // 8)), 0.35)
    img.save(dest, "JPEG", quality=92)


def variant_image(src: str, dest: str, size, seed: int):
    """A scene without its own image reuses a real one: a different crop, mirrored sometimes, and a
    colour tint, so it reads as another angle of the same world rather than a repeat."""
    W, H = size
    rnd = random.Random(seed)
    img = Image.open(src).convert("RGB")
    scale = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS)
    f = rnd.uniform(0.62, 0.78)
    cw, ch = int(img.width * f), int(img.height * f)
    x, y = rnd.randint(0, img.width - cw), rnd.randint(0, img.height - ch)
    img = img.crop((x, y, x + cw, y + ch)).resize((W, H), Image.LANCZOS)
    if rnd.random() < 0.5:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    tint = Image.new("RGB", (W, H), rnd.choice([(0, 150, 200), (120, 60, 200), (220, 120, 40)]))
    Image.blend(img, tint, 0.16).save(dest, "JPEG", quality=93)


ROOT = os.path.dirname(os.path.abspath(__file__))
PRODUCT_UI = os.environ.get("STUDIO_PRODUCT_UI", os.path.join(ROOT, "assets", "studio_ui.png"))
_UI_SCENE_RE = re.compile(r"prompt box|prompt bar|types itself|typing|types in|studio (?:ui|interface|screen|home)", re.I)
REGULAR_FONTS = [r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]


def _regular_font(size: int):
    from PIL import ImageFont
    for p in REGULAR_FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def mark_ui_scene(scenes):
    """The first scene about the prompt box shows the real Studio UI with the prompt typing in."""
    if not os.path.exists(PRODUCT_UI):
        return
    for s in scenes:
        if _UI_SCENE_RE.search(f"{s.get('visual', '')} {s.get('camera', '')}"):
            quotes = sorted(_quoted(s.get("visual", "")), key=len, reverse=True)
            s["ui"] = True
            s["typed"] = quotes[0] if quotes and len(quotes[0].split()) >= 4 else ""
            return


def render_ui_clip(dest: str, size, dur: float, typed: str, overlay_png: str):
    """Real Studio screenshot: the camera pushes in on the prompt box while the prompt types itself,
    then the Execute button pulses. Frames are drawn with PIL and piped to ffmpeg."""
    W, H = size
    ui = Image.open(PRODUCT_UI).convert("RGB")
    cfg_path = os.path.splitext(PRODUCT_UI)[0] + ".json"
    cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}
    if typed and cfg.get("text_box"):  # clear the prompt that is in the screenshot
        ImageDraw.Draw(ui).rectangle(cfg["text_box"], fill=tuple(cfg.get("fill_color", (19, 29, 53))))
    if W > H:  # cover the frame
        s = max(W / ui.width, H / ui.height)
        canvas = ui.resize((int(ui.width * s), int(ui.height * s)), Image.LANCZOS)
        ox, oy = (canvas.width - W) // 2, (canvas.height - H) // 2
        canvas = canvas.crop((ox, oy, ox + W, oy + H))
        ox, oy = -ox, -oy
    else:  # fit the width over a blurred copy
        s = W / ui.width
        fg = ui.resize((W, int(ui.height * s)), Image.LANCZOS)
        bs = H / ui.height
        canvas = ui.resize((int(ui.width * bs), H)).crop((0, 0, W, H)).filter(ImageFilter.GaussianBlur(30))
        ox, oy = 0, (H - fg.height) // 2
        canvas.paste(fg, (ox, oy))
    P = lambda x, y: (ox + x * s, oy + y * s)
    tb = cfg.get("text_box", [ui.width * 0.3, ui.height * 0.33, ui.width * 0.83, ui.height * 0.37])
    tx, ty = P(*cfg.get("text_origin", tb[:2]))
    font = _regular_font(max(12, int(cfg.get("font_px", 17) * s)))
    color = tuple(cfg.get("text_color", (235, 240, 255)))
    btn = cfg.get("button")
    focus = P((tb[0] + tb[2]) / 2, (tb[1] + (btn[3] if btn else tb[3])) / 2)

    N = max(int(round(dur * FPS)), 2)
    proc = subprocess.Popen(
        [FFMPEG_EXE, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", overlay_png,
         "-filter_complex", "[1:v]format=rgba,fade=t=in:st=0.25:d=0.45:alpha=1[ov];[0:v][ov]overlay=0:0:shortest=1,format=yuv420p[v]",
         "-map", "[v]", "-frames:v", str(N), "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
         "-pix_fmt", "yuv420p", dest], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for f in range(N):
            p = f / (N - 1)
            frame = canvas.copy()
            d = ImageDraw.Draw(frame)
            n = int(len(typed) * min(1.0, p / 0.7))
            shown = typed[:n]
            d.text((tx, ty), shown, font=font, fill=color)
            if p < 0.8 and int(f / (FPS * 0.5)) % 2 == 0:  # blinking cursor
                cx = tx + d.textlength(shown, font=font) + 2
                d.line([(cx, ty), (cx, ty + font.size * 1.15)], fill=color, width=max(2, font.size // 10))
            if btn and p > 0.78:  # Execute pulse
                k = (p - 0.78) / 0.22
                x0, y0 = P(btn[0], btn[1])
                x1, y1 = P(btn[2], btn[3])
                g = int(18 * s * k)
                d.rounded_rectangle([x0 - g, y0 - g, x1 + g, y1 + g], radius=int(14 * s),
                                    outline=(120, 220, 255), width=max(2, int(4 * s * (1 - k) + 1)))
            e = 0.5 - 0.5 * np.cos(np.pi * p)  # ease in-out push toward the prompt box
            z = 1.0 + 0.45 * e
            cx, cy = W / 2 + (focus[0] - W / 2) * e, H / 2 + (focus[1] - H / 2) * e
            cw, ch = W / z, H / z
            x0 = min(max(cx - cw / 2, 0), W - cw)
            y0 = min(max(cy - ch / 2, 0), H - ch)
            frame = frame.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize((W, H), Image.BILINEAR)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0:
            raise RuntimeError(err[-600:])
    finally:
        if proc.poll() is None:
            proc.kill()


def step_lines(scene: dict):
    """Montage narration like 'It writes the Terraform. Builds the repo.' -> animated step list."""
    if scene.get("title_text") or scene.get("ui"):
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", scene.get("narration", "")) if p.strip()]
    return parts if 2 <= len(parts) <= 5 and all(len(p) <= 60 for p in parts) else []


def _step_layers(work: str, i: int, size, steps, voice_len: float):
    """One transparent PNG per step (dark pill, green tick, text) and the second it appears."""
    from thumbnail_engine import _font
    W, H = size
    f = _font(int(min(W, H) * (0.048 if W > H else 0.04)))
    lh = int(f.size * 1.75)
    y0 = H // 2 - (lh * len(steps)) // 2
    x0 = int(W * 0.08)
    total_chars = sum(len(s) for s in steps) or 1
    paths, at, acc = [], [], 0
    for k, text in enumerate(steps):
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        y = y0 + k * lh
        tw = d.textlength(text, font=f)
        d.rounded_rectangle([x0 - 20, y - 14, x0 + f.size * 1.6 + tw + 30, y + f.size + 20],
                            radius=18, fill=(5, 12, 30, 190), outline=(0, 225, 255, 160), width=2)
        r = f.size // 2
        cx, cy = x0 + r, y + f.size // 2 + 3
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(30, 200, 110, 255))
        d.line([(cx - r * 0.5, cy), (cx - r * 0.1, cy + r * 0.45), (cx + r * 0.55, cy - r * 0.45)],
               fill=(255, 255, 255, 255), width=max(3, r // 4))
        d.text((x0 + f.size * 1.4, y), text, font=f, fill=(255, 255, 255, 255))
        p = os.path.join(work, f"step_{i:02d}_{k}.png")
        img.save(p)
        paths.append(p)
        at.append(0.3 + voice_len * acc / total_chars)
        acc += len(text)
    return paths, at


def _camera_kind(text: str, idx: int) -> str:
    t = (text or "").lower()
    for kind, words in (("crash", ("crash",)), ("dutch", ("dutch", "tilted", "handheld")),
                        ("pull", ("pull-back", "pull back", "pulling", "pull-out", "crane shot pulling", "pulls up")),
                        ("crane", ("crane", "tilt up", "rises", "rising", "low-angle", "low angle", "hero")),
                        ("pan_r", ("pan-right", "tracking", "side-scroll", "fpv", "flight", "glides", "orbit", "whip-pan", "drone")),
                        ("pan_l", ("pan-left",)),
                        ("push", ("push", "dolly", "zoom-in", "zoom in", "close-up", "macro", "extreme"))):
        if any(w in t for w in words):
            return kind
    return ("push", "pan_r", "pull", "crane", "pan_l")[idx % 5]


def _zoompan(kind: str, frames: int, W: int, H: int) -> str:
    N = max(frames - 1, 1)
    p = f"(on/{N})"
    zp = {
        "push": (f"1.0+0.16*{p}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        "pull": (f"1.18-0.16*{p}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        "pan_r": ("1.16", f"(iw-iw/zoom)*{p}", "ih/2-(ih/zoom/2)"),
        "pan_l": ("1.16", f"(iw-iw/zoom)*(1-{p})", "ih/2-(ih/zoom/2)"),
        "crane": ("1.16", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(1-{p})"),
        "crash": (f"1.0+0.4*min({p}*4\\,1)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        "dutch": (f"1.12+0.08*{p}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    }[kind]
    vf = f"zoompan=z='{zp[0]}':x='{zp[1]}':y='{zp[2]}':d=1:s={W}x{H}:fps={FPS}"
    if kind == "dutch":
        vf += f",rotate=a='0.06*sin(2*PI*t/6)':fillcolor=black,scale={int(W * 1.12)}:{int(H * 1.12)},crop={W}:{H}"
    return vf


def _transition(text: str) -> tuple:
    t = (text or "").lower()
    if any(w in t for w in ("black", "dip", "fade to")):
        return "fadeblack", 0.5
    if any(w in t for w in ("flash", "light burst", "light-leak", "white")):
        return "fadewhite", 0.35
    if any(w in t for w in ("whip", "swipe", "wipe")):
        return "slideleft", 0.35
    if "glitch" in t:
        return "pixelize", 0.35
    if any(w in t for w in ("zoom", "burst", "bursts", "flies")):
        return "zoomin", 0.45
    if any(w in t for w in ("smash", "hard cut", "cut on", "cut")):
        return "fade", 0.12
    return "fade", 0.5


def _overlay_png(dest: str, size, scene: dict, cinematic: bool, title_low: bool = False,
                 subtitle: bool = True):
    """Letterbox, act tag, title card and subtitle on a transparent layer (kept steady over the move).
    `title_low` puts the title card in the lower third (keeps the Studio UI visible)."""
    from thumbnail_engine import _font, _latin, _wrap
    W, H = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bar = int((H - W / 2.39) / 2) if cinematic and W > H else 0
    if bar:
        d.rectangle([0, 0, W, bar], fill=(0, 0, 0, 255))
        d.rectangle([0, H - bar, W, H], fill=(0, 0, 0, 255))
    else:  # vertical: soft dark bands so text reads on any image
        for i in range(int(H * 0.22)):
            a = int(170 * (1 - i / (H * 0.22)))
            d.line([(0, H - 1 - i), (W, H - 1 - i)], fill=(0, 0, 0, a))
    m = int(W * 0.04)
    if scene.get("act") and _latin(scene["act"]):
        f = _font(int(min(W, H) * 0.028))
        d.text((m, (bar or int(H * 0.04)) + int(min(W, H) * 0.03)), scene["act"], font=f,
               fill=(0, 225, 255, 235), stroke_width=2, stroke_fill=(0, 0, 0, 200))
    title = (scene.get("title_text") or "").strip()
    if title and _latin(title):
        size_px = int(min(W, H) * (0.085 if W > H else 0.075))
        while True:
            tf = _font(size_px)
            lines = [l for part in title.upper().split("\n") for l in _wrap(d, part, tf, int(W * 0.84))]
            if len(lines) <= 3 or size_px < 30:
                break
            size_px = int(size_px * 0.88)
        y = H // 2 - (len(lines) * int(size_px * 1.1)) // 2
        if title_low:
            size_px = int(size_px * 0.8)
            tf = _font(size_px)
            y = H - (bar or int(H * 0.06)) - int(H * 0.09) - len(lines[:3]) * int(size_px * 1.1)
        for line in lines[:3]:
            d.text((W // 2, y), line, font=tf, anchor="ma", fill=(255, 255, 255, 255),
                   stroke_width=max(3, size_px // 16), stroke_fill=(0, 0, 0, 230))
            y += int(size_px * 1.1)
        if scene.get("small_text") and _latin(scene["small_text"]):
            sf = _font(int(size_px * 0.42))
            d.text((W // 2, y + int(size_px * 0.2)), scene["small_text"], font=sf, anchor="ma",
                   fill=(255, 215, 90, 255), stroke_width=3, stroke_fill=(0, 0, 0, 230))
    sub = (scene.get("narration") or "").strip() if subtitle else ""
    if sub and _latin(sub):
        sf = _font(int(min(W, H) * (0.034 if W > H else 0.036)))
        lines = _wrap(d, sub, sf, int(W * 0.88))[:2]
        lh = int(sf.size * 1.2)
        y = (H - bar + int(bar * 0.12)) if bar and bar > lh * 2 else H - int(H * 0.06) - lh * len(lines)
        if bar and bar <= lh * 2:
            y = H - bar - lh * len(lines) - int(H * 0.02)
        for line in lines:
            d.text((W // 2, y), line, font=sf, anchor="ma", fill=(255, 255, 255, 255),
                   stroke_width=3, stroke_fill=(0, 0, 0, 255))
            y += lh
    img.save(dest, "PNG")


_TRIM = ("silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
         "silenceremove=start_periods=1:start_threshold=-45dB,areverse")


async def _tts(text: str, dest: str, voice: str, rate: int):
    """Voice line -> WAV with Edge-TTS's leading/trailing silence trimmed (about 0.6 s per line,
    which otherwise adds ~10 s of dead air to a 16-line trailer). '...' becomes a short pause."""
    import edge_tts
    raw = dest + ".mp3"
    text = re.sub(r"\s*(?:\.\.\.|…)\s*", ", ", text).strip(" ,")
    await edge_tts.Communicate(text, voice=voice, rate=f"{rate:+d}%", pitch="-6Hz").save(raw)
    _run([FFMPEG_EXE, "-y", "-i", raw, "-af", _TRIM, "-ar", str(SR), "-ac", "2", dest])
    os.remove(raw)


def _sfx_track(events, total: float, dest: str):
    """Whoosh / boom / riser / chime events [(time, kind)] synthesised into one stereo WAV."""
    n = int(total * SR) + SR
    out = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(7)
    for t0, kind in events:
        i0 = int(max(t0, 0) * SR)
        if kind == "boom":
            L = int(1.8 * SR)
            t = np.arange(L) / SR
            s = 0.9 * np.sin(2 * np.pi * (55 - 25 * t) * t) * np.exp(-2.2 * t)
            s += 0.25 * rng.standard_normal(L) * np.exp(-14 * t)
        elif kind == "riser":
            L = int(1.2 * SR)
            t = np.arange(L) / SR
            s = 0.25 * rng.standard_normal(L) * (t / t[-1]) ** 2
            s = np.convolve(s, np.ones(8) / 8, mode="same")
        elif kind == "chime":
            L = int(1.2 * SR)
            t = np.arange(L) / SR
            s = 0.18 * (np.sin(2 * np.pi * 1318 * t) + 0.6 * np.sin(2 * np.pi * 1976 * t)) * np.exp(-4 * t)
        else:  # whoosh: band-passed noise swelling and fading
            L = int(0.7 * SR)
            t = np.arange(L) / SR
            env = np.sin(np.pi * t / t[-1]) ** 2
            s = rng.standard_normal(L) * env
            s = np.convolve(s, np.ones(24) / 24, mode="same") * 0.9
        i1 = min(n, i0 + len(s))
        out[i0:i1] += s[: i1 - i0]
    out = np.clip(out, -1, 1)
    wavfile.write(dest, SR, (np.stack([out, out], axis=1) * 32767).astype(np.int16))


def _sfx_kind(text: str):
    t = (text or "").lower()
    if any(w in t for w in ("boom", "braa", "impact", "slam", "stamp", "drum hit", "hit", "shockwave", "clang")):
        return "boom"
    if any(w in t for w in ("riser", "swell", "rising", "build")):
        return "riser"
    if any(w in t for w in ("chime", "ding", "success", "bleep")):
        return "chime"
    if any(w in t for w in ("whoosh", "swoosh", "whip")):
        return "whoosh"
    return None


# ----------------------------------------------------------------------------- main renderer

async def render_hollywood_video(prompt: str, output_mp4_path: str, script_info: dict = None,
                                 music_info: dict = None, on_log=None, aspect: str = None,
                                 target_seconds: float = None, image_generator=None) -> str:
    """`image_generator`: optional async callable(jobs) that writes an image for each job
    ({index, prompt, path, size}) to job["path"]; missing or broken files fall back."""
    log = on_log or (lambda m: print(f"[hollywood] {m}"))
    aspect = aspect or aspect_from_prompt(prompt)
    W, H = SIZES.get(aspect, SIZES["16:9"])
    target = target_seconds or duration_from_prompt(prompt)
    hindi = "hindi" in (prompt or "").lower()
    voice = "hi-IN-MadhurNeural" if hindi else os.environ.get("STUDIO_TRAILER_VOICE", "en-US-GuyNeural")

    scenes, style = parse_shot_list(prompt)
    source = "shot list"
    if not scenes:
        loop = asyncio.get_running_loop()
        try:
            scenes = await asyncio.wait_for(loop.run_in_executor(None, gemini_scene_plan, prompt, target), 60)
            source = "gemini"
        except Exception as e:
            log(f"Gemini scene plan failed ({type(e).__name__}), using template")
            scenes = None
    if not scenes:
        scenes, source = template_scene_plan(prompt, target), "template"
    style = style or DEFAULT_STYLE
    mark_ui_scene(scenes)
    log(f"Scene plan: {len(scenes)} scenes from {source}, {aspect} {W}x{H}, target {target:.0f} s"
        + (" (prompt-box scene uses the real Studio UI)" if any(s.get("ui") for s in scenes) else ""))

    work = tempfile.mkdtemp(prefix="hollywood_")
    try:
        # 1. One image per scene (not for the Studio UI scene). The caller's image generator (the
        #    Studio passes agy) writes the files; Pollinations only if STUDIO_IMAGE_PROVIDER=pollinations
        #    (free tier is slow and watermarked). A scene still without an image reuses the nearest
        #    real image as a new angle; the designed backdrop is the last resort.
        jobs = [{"index": i, "path": os.path.join(work, f"gen_{i:02d}.png"), "size": (W, H),
                 "prompt": f"{s['visual']}. {style}. No text, no letters, no logos, no watermark."}
                for i, s in enumerate(scenes)
                if not s.get("ui") and s.get("img_group", i) == i]  # split parts share one image
        if image_generator:
            try:
                await image_generator(jobs)
            except Exception as e:
                log(f"Image generator failed: {type(e).__name__} {str(e)[:160]}")
        elif os.environ.get("STUDIO_IMAGE_PROVIDER", "").lower() == "pollinations":
            for j in jobs:
                fetch_scene_image(j["prompt"], (W, H), j["path"], seed=1000 + j["index"] * 17)
        real, missing = [], []
        for j in jobs:
            dest = os.path.join(work, f"img_{j['index']:02d}.jpg")
            try:
                Image.open(j["path"]).convert("RGB").save(dest, "JPEG", quality=94)
                real.append(j["index"])
            except Exception:
                missing.append(j["index"])
        job_idx = {j["index"] for j in jobs}
        shared = [i for i, s in enumerate(scenes) if not s.get("ui") and i not in job_idx]
        for i in sorted(missing + shared):
            dest = os.path.join(work, f"img_{i:02d}.jpg")
            group = scenes[i].get("img_group")
            if group is not None and group in real:
                src = group  # another angle of its own shot's image
            elif real:
                src = min(real, key=lambda r: (abs(r - i), r > i))  # nearest, earlier one on ties
            else:
                fallback_scene_image(dest, (W, H), i, scenes[i].get("title_text", ""))
                continue
            variant_image(os.path.join(work, f"img_{src:02d}.jpg"), dest, (W, H), seed=i)
        ai_ok = [True] * len(real)
        log(f"Scene images: {len(real)} AI-generated, {len(missing) + len(shared)} reused as new angles"
            + (" (no AI images: designed backdrops)" if missing and not real else ""))

        # 2. Voiceover per scene at a natural pace (the target length is a guide, running longer is
        #    fine); only a script far over the target gets a slight speed-up.
        spoken = [i for i, s in enumerate(scenes) if s.get("narration")]
        rate, voice_len = int(os.environ.get("STUDIO_TRAILER_RATE", "5")), {}
        for attempt in range(2):
            for i in spoken:
                await _tts(scenes[i]["narration"], os.path.join(work, f"vo_{i:02d}.wav"), voice, rate)
                voice_len[i] = _duration(os.path.join(work, f"vo_{i:02d}.wav"))
            need = sum(voice_len.values()) + 0.45 * len(spoken)
            if need <= target * 1.25 or attempt:
                break
            rate += 10
            log(f"Script is long for {target:.0f} s: voiceover at {rate:+d}%")

        # 3. Scene lengths: the shot's own timing, never shorter than its voice line; padded up to
        #    the target when the script is short.
        # A zoom out of the Studio UI scene would zoom into the empty prompt box: fade instead.
        trans = [("fade", 0.4) if s.get("ui") else _transition(s.get("transition", "")) for s in scenes]
        durs = [max(s.get("dur") or 0, voice_len.get(i, 0) + 0.45 + (0.3 if i else 0), 1.8)
                for i, s in enumerate(scenes)]
        total = sum(durs) - sum(t[1] for t in trans[:-1])
        if total < target:
            durs = [d + (target - total) * d / sum(durs) for d in durs]

        # 4. Render the scenes, several at once (zoompan is single-threaded, so one ffmpeg per core
        #    is much faster than one at a time): the Studio UI scene types its prompt; the others get
        #    a camera move on the image, a steady text layer and, for montage lines, a step list.
        def render_scene(i):
            s = scenes[i]
            frames = int(round(durs[i] * FPS))
            clip = os.path.join(work, f"clip_{i:02d}.mp4")
            ov = os.path.join(work, f"ov_{i:02d}.png")
            if s.get("ui"):
                _overlay_png(ov, (W, H), s, cinematic=True, title_low=True)
                render_ui_clip(clip, (W, H), durs[i], s.get("typed", ""), ov)
                return clip, "Studio UI, prompt typing"
            steps = step_lines(s)
            img = os.path.join(work, f"img_{i:02d}.jpg")
            big = os.path.join(work, f"big_{i:02d}.jpg")
            Image.open(img).convert("RGB").resize((int(W * 1.3), int(H * 1.3)), Image.LANCZOS).save(big, quality=93)
            _overlay_png(ov, (W, H), s, cinematic=True, subtitle=not steps)
            kind = _camera_kind(s.get("camera", ""), i)
            inputs = ["-loop", "1", "-framerate", str(FPS), "-t", f"{durs[i]:.3f}", "-i", big,
                      "-loop", "1", "-framerate", str(FPS), "-t", f"{durs[i]:.3f}", "-i", ov]
            graph = (f"[0:v]{_zoompan(kind, frames, W, H)},eq=contrast=1.06:saturation=1.08,format=yuv420p[bg];"
                     f"[1:v]format=rgba,fade=t=in:st=0.25:d=0.45:alpha=1[ov];"
                     f"[bg][ov]overlay=0:0:shortest=1[t0];")
            layers, at = _step_layers(work, i, (W, H), steps, voice_len.get(i, durs[i] - 0.6)) if steps else ([], [])
            for k, (p, t0) in enumerate(zip(layers, at)):
                inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{durs[i]:.3f}", "-i", p]
                graph += (f"[{k + 2}:v]format=rgba,fade=t=in:st={t0:.2f}:d=0.3:alpha=1[s{k}];"
                          f"[t{k}][s{k}]overlay=0:0:shortest=1[t{k + 1}];")
            graph += f"[t{len(layers)}]format=yuv420p[v]"
            _run([FFMPEG_EXE, "-y"] + inputs +
                 ["-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames), "-r", str(FPS)]
                 + X264 + ["-threads", "2", clip])
            return clip, f"{kind}{', step list' if steps else ''}"

        workers = int(os.environ.get("STUDIO_RENDER_WORKERS", max(1, min(4, os.cpu_count() or 1))))
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [loop.run_in_executor(pool, render_scene, i) for i in range(len(scenes))]
            results = []
            for i, fut in enumerate(futures):
                results.append(await fut)
                log(f"Scene {i + 1}/{len(scenes)} rendered ({results[-1][1]}, {durs[i]:.1f} s)")
        clip_paths = [r[0] for r in results]

        # 5. Join with transitions; work out where each scene starts on the final timeline.
        starts, t = [0.0], durs[0]
        for i in range(1, len(scenes)):
            t -= trans[i - 1][1]
            starts.append(t)
            t += durs[i]
        total = t
        inputs, graph, prev = [], "", "[0:v]"
        for p in clip_paths:
            inputs += ["-i", p]
        for i in range(1, len(clip_paths)):
            name, d = trans[i - 1]
            out = f"[x{i}]" if i < len(clip_paths) - 1 else "[v]"
            graph += f"{prev}[{i}:v]xfade=transition={name}:duration={d}:offset={starts[i]:.3f}{out};"
            prev = out
        video_only = os.path.join(work, "video.mp4")
        if len(clip_paths) == 1:
            graph, inputs = "[0:v]null[v]", ["-i", clip_paths[0]]
        # Fade to black at the very end, so the cached outro (which fades in from black) can be
        # joined without re-encoding.
        graph = graph.rstrip(";").replace("[v]", "[vx]") + f";[vx]fade=t=out:st={max(total - 0.4, 0):.3f}:d=0.4[v]"
        _run([FFMPEG_EXE, "-y"] + inputs + ["-filter_complex", graph, "-map", "[v]", "-r", str(FPS)]
             + X264 + [video_only])

        # 6. Audio: voice lines at their scene starts, ducked music, sound effects.
        from music_engine import build_soundtrack, detect_mood
        music = os.path.join(work, "music.wav")
        info = build_soundtrack(prompt, total, music, mood=detect_mood(prompt))
        if music_info is not None:
            music_info.update(info)
        events = []
        for i, s in enumerate(scenes):
            k = _sfx_kind(s.get("sfx", ""))
            if k:
                events.append((starts[i] + (0.0 if k == "boom" else 0.1), k))
            if i and trans[i - 1][1] >= 0.3:
                events.append((starts[i] - 0.25, "whoosh"))
        sfx = os.path.join(work, "sfx.wav")
        _sfx_track(events, total, sfx)

        main_film = os.path.join(work, "main.mp4")
        a_inputs, a_parts, labels = ["-i", music, "-i", sfx], [], []
        fmt = f"aformat=sample_fmts=fltp:sample_rates={SR}:channel_layouts=stereo"
        for n, i in enumerate(spoken):
            a_inputs += ["-i", os.path.join(work, f"vo_{i:02d}.wav")]
            ms = int((starts[i] + 0.3) * 1000)
            a_parts.append(f"[{n + 2}:a]{fmt},adelay={ms}|{ms}[v{n}]")
            labels.append(f"[v{n}]")
        if labels:
            voice_mix = (";".join(a_parts) + ";" + "".join(labels) +
                         f"amix=inputs={len(labels)}:duration=longest:normalize=0,apad,atrim=0:{total:.3f},asplit=2[vm][vk];")
        else:
            voice_mix = f"anullsrc=r={SR}:cl=stereo,atrim=0:{total:.3f},asplit=2[vm][vk];"
        agraph = (voice_mix +
                  f"[0:a]{fmt},volume=0.75[mus];[mus][vk]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=450[duck];"
                  f"[1:a]{fmt},volume=0.55,atrim=0:{total:.3f}[fx];"
                  f"[vm][duck][fx]amix=inputs=3:duration=first:normalize=0,"
                  f"afade=t=out:st={max(total - 1.5, 0):.3f}:d=1.5,loudnorm=I=-14:TP=-1.5:LRA=11,aresample={SR}[a]")
        _run([FFMPEG_EXE, "-y", "-i", video_only] + a_inputs +
             ["-filter_complex", _shift_inputs(agraph, 1), "-map", "0:v", "-map", "[a]", "-c:v", "copy"]
             + AAC + ["-t", f"{total:.3f}", "-video_track_timescale", TIMESCALE, main_film])
        log(f"Main film: {total:.1f} s, {len(scenes)} scenes")

        # 7. Studio outro (the 9:16 promo clip, centred over its blurred copy on 16:9) + end card
        #    for this shape. Built once per shape and cached, then joined without re-encoding.
        tail = None
        try:
            tail = cached_tail((W, H), log)
        except Exception as e:
            log(f"outro/end card skipped: {str(e)[:200]}")
        if tail:
            lst = os.path.join(work, "concat.txt")
            with open(lst, "w", encoding="utf-8") as f:
                f.write(f"file '{_concat_path(main_film)}'\nfile '{_concat_path(tail)}'\n")
            _run([FFMPEG_EXE, "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
                  "-movflags", "+faststart", output_mp4_path])
            if script_info is not None:
                script_info.update(outro=True, endcard=True)
        else:
            _run([FFMPEG_EXE, "-y", "-i", main_film, "-c", "copy", "-movflags", "+faststart", output_mp4_path])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if script_info is not None:
        script_info.update({"scenes": len(scenes), "plan_source": source, "size": (W, H),
                            "main_seconds": round(total, 1), "ai_images": sum(ai_ok),
                            "title": (scenes[-1].get("title_text") or "AI Orchestration Studio")})
    return output_mp4_path


# Same encoder settings for the main film and the cached tail, so they concatenate losslessly.
X264 = ["-c:v", "libx264", "-preset", os.environ.get("STUDIO_X264_PRESET", "superfast"), "-crf", "20",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1"]
AAC = ["-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", "2"]
TIMESCALE = "12800"
TAIL_VERSION = "1"


def _concat_path(p: str) -> str:
    return p.replace("\\", "/").replace("'", "'\\''")


def cached_tail(size, log=print):
    """Outro clip + end card for this shape, rendered once and cached under assets/cache (rebuilt
    when the outro clip, the card or the shape changes). Starts from black, so a main film that
    fades to black joins it with a plain stream copy."""
    import hashlib
    from thumbnail_engine import OUTRO_9X16, ENDCARD_16X9, ENDCARD_9X16
    W, H = size
    outro = os.environ.get("STUDIO_HOLLYWOOD_OUTRO", OUTRO_9X16)
    card = ENDCARD_16X9 if W >= H else ENDCARD_9X16
    use_outro = os.environ.get("STUDIO_VIDEO_OUTRO", "1") != "0" and os.path.exists(outro)
    use_card = os.environ.get("STUDIO_VIDEO_ENDCARD", "1") != "0" and os.path.exists(card)
    if not (use_outro or use_card):
        return None
    card_s = float(os.environ.get("STUDIO_ENDCARD_SECONDS", "4"))
    sig = "|".join([TAIL_VERSION, f"{W}x{H}", str(FPS), str(card_s), " ".join(X264)] +
                   [f"{p}:{os.path.getsize(p)}:{int(os.path.getmtime(p))}" for p, u in ((outro, use_outro), (card, use_card)) if u])
    cache_dir = os.environ.get("STUDIO_VIDEO_CACHE", os.path.join(ROOT, "assets", "cache"))
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"tail_{W}x{H}_{hashlib.md5(sig.encode()).hexdigest()[:10]}.mp4")
    if os.path.exists(out):
        return out
    log("Building the outro + end card once for this shape (cached for next videos)...")
    fmt = f"setsar=1,fps={FPS},format=yuv420p"
    afmt = f"aformat=sample_fmts=fltp:sample_rates={SR}:channel_layouts=stereo"
    inputs, parts, labels = [], [], []
    if use_outro:
        has_audio = "Audio:" in subprocess.run([FFMPEG_EXE, "-i", outro], stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, text=True, errors="replace").stderr
        d = _duration(outro)
        inputs += ["-i", outro]
        n = len(inputs) // 2 - 1
        parts.append(f"[{n}:v]split[o1][o2];[o1]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                     f"gblur=sigma=30,eq=brightness=-0.12[ob];[o2]scale={W}:{H}:force_original_aspect_ratio=decrease[of];"
                     f"[ob][of]overlay=(W-w)/2:(H-h)/2,{fmt},fade=t=in:st=0:d=0.4[ov]")
        parts.append(f"[{n}:a]{afmt},atrim=0:{d:.3f}[oa]" if has_audio else
                     f"anullsrc=r={SR}:cl=stereo,atrim=0:{d:.3f}[oa]")
        labels.append("[ov][oa]")
    if use_card:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{card_s:.2f}", "-i", card]
        n = sum(1 for x in inputs if x == "-i") - 1
        parts.append(f"[{n}:v]split[c1][c2];[c1]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                     f"gblur=sigma=30,eq=brightness=-0.15[cb];[c2]scale={W}:{H}:force_original_aspect_ratio=decrease[cf];"
                     f"[cb][cf]overlay=(W-w)/2:(H-h)/2,{fmt},fade=t=in:st=0:d=0.5[cv]")
        parts.append(f"anullsrc=r={SR}:cl=stereo,atrim=0:{card_s:.2f}[ca]")
        labels.append("[cv][ca]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=1:a=1[v][a]"
    tmp = out + ".tmp.mp4"
    _run([FFMPEG_EXE, "-y"] + inputs + ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-r", str(FPS)]
         + X264 + AAC + ["-video_track_timescale", TIMESCALE, tmp])
    os.replace(tmp, out)
    return out


def _shift_inputs(graph: str, by: int) -> str:
    """The audio graph is written with music=[0:a], sfx=[1:a], voices=[2..]; the real command has
    the video as input 0, so every [n:a] label moves up by `by`."""
    return re.sub(r"\[(\d+):a\]", lambda m: f"[{int(m.group(1)) + by}:a]", graph)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Render a Hollywood-style multi-scene promo from a brief.")
    ap.add_argument("brief", help="text file with the brief / shot list, or the brief itself")
    ap.add_argument("--out", default="hollywood_promo.mp4")
    a = ap.parse_args()
    text = open(a.brief, encoding="utf-8").read() if os.path.exists(a.brief) else a.brief
    si = {}
    asyncio.run(render_hollywood_video(text, a.out, script_info=si))
    print(json.dumps(si, indent=2))
