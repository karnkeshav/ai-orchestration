"""Thumbnails for Studio videos.

make_thumbnail()      -> a YouTube-style 1920x1080 (or 1080x1920) JPG: blurred scene backdrop,
                         mood colours, big outlined title, short hook line, mood tag.
prepend_thumbnail()   -> puts that thumbnail at the start of any MP4 (held for N seconds, then a
                         cross-fade into the video, audio shifted to match) and embeds it as the
                         file's cover art, so players, WhatsApp and file browsers show it too.

Works for any video, not only the hybrid engine:
    python thumbnail_engine.py in.mp4 --title "AI Saves the Cloud" --hook "One agent. Zero downtime." --mood tech
"""
import os
import re
import shutil
import subprocess
import tempfile

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

MOOD_STYLE = {  # (accent colour, gradient top, gradient bottom, tag)
    "tech": ((0, 220, 255), (8, 18, 48), (20, 60, 120), "AI & TECH STORY"),
    "inspiring": ((80, 230, 160), (10, 30, 40), (20, 90, 90), "INSPIRING STORY"),
    "mystery": ((190, 120, 255), (12, 8, 30), (50, 20, 80), "MYSTERY STORY"),
    "emotional": ((255, 170, 90), (30, 15, 40), (90, 40, 80), "EMOTIONAL STORY"),
    "festive": ((255, 200, 0), (50, 10, 30), (140, 40, 30), "FESTIVE STORY"),
    "happy": ((255, 220, 60), (20, 40, 90), (40, 120, 160), "FUN STORY"),
    "adventure": ((255, 140, 40), (15, 30, 30), (40, 90, 60), "ADVENTURE STORY"),
}
DEFAULT_STYLE = MOOD_STYLE["inspiring"]

BOLD_FONTS = [
    os.environ.get("STUDIO_THUMB_FONT", ""),
    r"C:\Windows\Fonts\seguibl.ttf", r"C:\Windows\Fonts\impact.ttf", r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _font(size: int):
    for path in BOLD_FONTS:
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _latin(text: str) -> bool:
    """Bundled bold fonts have no Devanagari glyphs; skip text they would draw as boxes."""
    return all(ord(c) < 0x2000 for c in text)


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _background_image(background, size):
    """Accepts an image path or a video path (grabs a frame at 1 s)."""
    if not background or not os.path.exists(background):
        return None
    if background.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        subprocess.run([FFMPEG_EXE, "-y", "-ss", "1", "-i", background, "-frames:v", "1", tmp],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        background = tmp
    try:
        img = Image.open(background).convert("RGB")
    except Exception:
        return None
    # cover-fit
    scale = max(size[0] / img.width, size[1] / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS)
    left, top = (img.width - size[0]) // 2, (img.height - size[1]) // 2
    return img.crop((left, top, left + size[0], top + size[1]))


def make_thumbnail(title: str, out_path: str, hook: str = None, mood: str = None,
                   background: str = None, size=(1920, 1080), brand: str = "AI ORCHESTRATION STUDIO",
                   question: str = None) -> str:
    """With `question`, the thumbnail is a puzzle hook: the question is the headline, a giant
    '?' fills the right side, and the hook line says the answer is at the end of the video."""
    accent, top_c, bot_c, tag = MOOD_STYLE.get(mood, DEFAULT_STYLE)
    W, H = size
    vertical = H > W
    if question and _latin(question):
        title, tag = question, "CAN YOU GUESS?"
        hook = hook or "Watch till the end for the answer!"

    # Backdrop: mood gradient, blended with the blurred scene if there is one.
    base = Image.new("RGB", size)
    d = ImageDraw.Draw(base)
    for y in range(H):
        r = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top_c[i] * (1 - r) + bot_c[i] * r) for i in range(3)))
    scene = _background_image(background, size)
    if scene:
        base = Image.blend(base, scene.filter(ImageFilter.GaussianBlur(W // 160)), 0.55)

    # Readability: darken the text side.
    shade = Image.new("L", size)
    sd = ImageDraw.Draw(shade)
    for i in range(H if vertical else W):
        a = int(200 * max(0.0, 1 - i / ((H if vertical else W) * 0.75)))
        if vertical:
            sd.line([(0, H - 1 - i), (W, H - 1 - i)], fill=a)
        else:
            sd.line([(i, 0), (i, H)], fill=a)
    base = Image.composite(Image.new("RGB", size, (0, 0, 0)), base, shade)

    # Glow orb in the accent colour.
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy, R = (int(W * 0.78), int(H * 0.38), int(min(W, H) * 0.34)) if not vertical else (W // 2, int(H * 0.25), int(W * 0.45))
    gd.ellipse([cx - R, cy - R, cx + R, cy + R], fill=accent + (90,))
    glow = glow.filter(ImageFilter.GaussianBlur(R // 2))
    img = Image.alpha_composite(base.convert("RGBA"), glow)
    draw = ImageDraw.Draw(img)

    if tag == "CAN YOU GUESS?":  # giant question mark over the glow
        qf = _font(int(R * 1.9))
        bbox = draw.textbbox((0, 0), "?", font=qf)
        qx, qy = cx - (bbox[0] + bbox[2]) // 2, cy - (bbox[1] + bbox[3]) // 2
        draw.text((qx, qy), "?", font=qf, fill=accent + (255,), stroke_width=max(6, R // 30), stroke_fill=(0, 0, 0))

    margin = int(W * 0.06)
    text_w = int(W * (0.86 if vertical else 0.62))

    # Mood tag chip.
    tag_font = _font(int(H * (0.024 if vertical else 0.034)))
    tw = draw.textlength(tag, font=tag_font)
    ty = int(H * (0.52 if vertical else 0.12))
    pad = int(tag_font.size * 0.55)
    draw.rounded_rectangle([margin, ty, margin + tw + 2 * pad, ty + tag_font.size + 2 * pad],
                           radius=pad, fill=accent + (255,))
    draw.text((margin + pad, ty + pad * 0.8), tag, font=tag_font, fill=(10, 10, 20))

    # Title: largest size that fits in 3 lines.
    title = (title or "").strip() or "New Story"
    if not _latin(title):
        title = tag.title()
    size_px = int(H * (0.075 if vertical else 0.13))
    while True:
        tf = _font(size_px)
        lines = _wrap(draw, title.upper(), tf, text_w)
        if len(lines) <= 3 or size_px < 40:
            break
        size_px = int(size_px * 0.9)
    y = ty + tag_font.size + 2 * pad + int(H * 0.03)
    stroke = max(3, size_px // 14)
    for i, line in enumerate(lines[:3]):
        color = accent if i == len(lines[:3]) - 1 and len(lines) > 1 else (255, 255, 255)
        draw.text((margin, y), line, font=tf, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += int(size_px * 1.08)

    # Hook line.
    if hook and _latin(hook):
        hf = _font(int(size_px * 0.36))
        hook_lines = _wrap(draw, hook.strip(), hf, text_w)[:2]
        y += int(H * 0.015)
        for line in hook_lines:
            draw.text((margin, y), line, font=hf, fill=(235, 240, 255), stroke_width=2, stroke_fill=(0, 0, 0))
            y += int(hf.size * 1.25)

    # Brand strip + accent bar.
    bf = _font(int(H * (0.018 if vertical else 0.026)))
    draw.rectangle([0, H - int(H * 0.012), W, H], fill=accent + (255,))
    draw.text((margin, H - int(H * 0.012) - bf.size - int(H * 0.03)), brand, font=bf, fill=(220, 230, 255))

    img.convert("RGB").save(out_path, "JPEG", quality=90)
    return out_path


def _probe(video: str):
    """-> (width, height, fps, has_audio, duration_sec)"""
    out = subprocess.run([FFMPEG_EXE, "-i", video], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, errors="replace").stderr
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", out)
    f = re.search(r"(\d+(?:\.\d+)?) fps", out)
    dm = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out)
    dur = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3)) if dm else 0.0
    return (int(m.group(1)), int(m.group(2)), float(f.group(1)) if f else 25.0, "Audio:" in out, dur)


ROOT = os.path.dirname(os.path.abspath(__file__))
OUTRO_16X9 = os.environ.get("STUDIO_OUTRO_16X9", os.path.join(ROOT, "assets", "studio_outro_16x9.mp4"))
OUTRO_9X16 = os.environ.get("STUDIO_OUTRO_9X16", os.path.join(ROOT, "assets", "studio_outro_9x16.mp4"))


def append_outro(video_in: str, video_out: str, outro: str = None, fade: float = 0.4) -> str:
    """Append the studio outro clip: the 16:9 clip for landscape videos, the 9:16 clip for
    vertical ones. The clip is fitted over a blurred copy of itself, so any aspect ratio works.
    Returns video_out, or None (video_in untouched) if no outro clip is available."""
    w, h, fps, has_audio, dur = _probe(video_in)
    outro = outro or (OUTRO_16X9 if w >= h else OUTRO_9X16)
    if not outro or not os.path.exists(outro) or dur <= fade:
        return None
    _, _, _, outro_audio, outro_dur = _probe(outro)
    fmt = f"setsar=1,fps={fps},format=yuv420p,settb=AVTB"
    graph = (f"[1:v]split[o1][o2];"
             f"[o1]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=30,eq=brightness=-0.12[bg];"
             f"[o2]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
             f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{fmt}[ov];"
             f"[0:v]{fmt}[mv];"
             f"[mv][ov]xfade=transition=fade:duration={fade}:offset={dur - fade:.3f}[v];")
    afmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    main_a = f"[0:a]{afmt}[ma];" if has_audio else f"anullsrc=r=44100:cl=stereo,atrim=duration={dur:.3f}[ma];"
    outro_a = f"[1:a]{afmt}[oa];" if outro_audio else f"anullsrc=r=44100:cl=stereo,atrim=duration={outro_dur:.3f}[oa];"
    graph += main_a + outro_a + f"[ma][oa]acrossfade=d={fade}[a]"
    tmp_out = video_out + ".outro.mp4"
    cmd = [FFMPEG_EXE, "-y", "-i", video_in, "-i", outro, "-filter_complex", graph,
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp_out]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    if res.returncode != 0:
        raise RuntimeError(res.stderr[-800:])
    shutil.move(tmp_out, video_out)
    return video_out


ENDCARD_16X9 = os.environ.get("STUDIO_ENDCARD_16X9", os.path.join(ROOT, "assets", "studio_endcard_16x9.png"))
ENDCARD_9X16 = os.environ.get("STUDIO_ENDCARD_9X16", os.path.join(ROOT, "assets", "studio_endcard_9x16.png"))


def append_endcard(video_in: str, video_out: str, image: str = None, seconds: float = 4.0,
                   fade: float = 0.5) -> str:
    """Hold the like / follow / share end card as the last `seconds` of the video (16:9 or 9:16
    card picked by the video's shape). Returns video_out, or None if no card is available."""
    w, h, fps, has_audio, dur = _probe(video_in)
    image = image or (ENDCARD_16X9 if w >= h else ENDCARD_9X16)
    if not image or not os.path.exists(image) or dur <= fade:
        return None
    fmt = f"setsar=1,fps={fps},format=yuv420p,settb=AVTB"
    graph = (f"[1:v]split[c1][c2];"
             f"[c1]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=30,eq=brightness=-0.15[bg];"
             f"[c2]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
             f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{fmt}[card];"
             f"[0:v]{fmt}[mv];"
             f"[mv][card]xfade=transition=fade:duration={fade}:offset={dur - fade:.3f}[v]")
    total = dur - fade + seconds + fade
    maps = ["-map", "[v]"]
    if has_audio:  # the outro's audio fades out, then silence under the card
        graph += f";[0:a]afade=t=out:st={max(0.0, dur - 1.0):.3f}:d=1,apad=whole_dur={total:.3f}[a]"
        maps += ["-map", "[a]"]
    tmp_out = video_out + ".end.mp4"
    cmd = [FFMPEG_EXE, "-y", "-i", video_in, "-loop", "1", "-t", f"{seconds + fade:.2f}", "-i", image,
           "-filter_complex", graph] + maps + [
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", tmp_out]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    if res.returncode != 0:
        raise RuntimeError(res.stderr[-800:])
    shutil.move(tmp_out, video_out)
    return video_out


def prepend_thumbnail(video_in: str, thumb: str, video_out: str, seconds: float = 1.5,
                      fade: float = 0.5, embed_cover: bool = True) -> str:
    """Hold `thumb` for `seconds`, cross-fade into `video_in`, shift its audio to match,
    and (optionally) embed `thumb` as cover art. video_in and video_out may be the same path."""
    w, h, fps, has_audio, _ = _probe(video_in)
    hold = seconds + fade
    vf = (f"[1:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
          f"fps={fps},format=yuv420p,settb=AVTB[t];"
          f"[0:v]scale={w}:{h},setsar=1,fps={fps},format=yuv420p,settb=AVTB[m];"
          f"[t][m]xfade=transition=fade:duration={fade}:offset={seconds}[v]")
    maps = ["-map", "[v]"]
    if has_audio:
        ms = int(seconds * 1000)
        vf += f";[0:a]adelay={ms}|{ms}[a]"
        maps += ["-map", "[a]"]
    tmp_out = video_out + ".tmp.mp4"
    cmd = [FFMPEG_EXE, "-y", "-i", video_in, "-loop", "1", "-t", f"{hold:.2f}", "-i", thumb]
    if embed_cover:
        cmd += ["-i", thumb]
    cmd += ["-filter_complex", vf] + maps
    if embed_cover:
        cmd += ["-map", "2:v"]
    cmd += ["-c:v:0", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    if embed_cover:
        cmd += ["-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic"]
    cmd += ["-movflags", "+faststart", tmp_out]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    if res.returncode != 0 and embed_cover:
        return prepend_thumbnail(video_in, thumb, video_out, seconds, fade, embed_cover=False)
    if res.returncode != 0:
        raise RuntimeError(res.stderr[-800:])
    shutil.move(tmp_out, video_out)
    return video_out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Add a thumbnail opening + cover art to any MP4.")
    ap.add_argument("video")
    ap.add_argument("--title", required=True)
    ap.add_argument("--hook")
    ap.add_argument("--mood", choices=list(MOOD_STYLE))
    ap.add_argument("--out", help="output MP4 (default: overwrite input)")
    ap.add_argument("--seconds", type=float, default=1.5)
    ap.add_argument("--vertical", action="store_true", help="1080x1920 reel thumbnail")
    a = ap.parse_args()
    thumb = os.path.splitext(a.out or a.video)[0] + "_thumb.jpg"
    make_thumbnail(a.title, thumb, hook=a.hook, mood=a.mood, background=a.video,
                   size=(1080, 1920) if a.vertical else (1920, 1080))
    print(prepend_thumbnail(a.video, thumb, a.out or a.video, seconds=a.seconds), thumb)
