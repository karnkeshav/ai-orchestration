import os
import re
import sys
import time
import asyncio
import numpy as np
from scipy.io import wavfile
from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg
import subprocess
import json

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

# 3D Pixar & Disney Stylized Themes
PALETTES = {
    "pixar_warm": {
        "bg_top": (25, 20, 45),
        "bg_bottom": (70, 35, 90),
        "accent": (255, 180, 50),
        "glow": (255, 110, 80),
        "text": (255, 255, 255),
        "card_bg": (40, 25, 65, 220)
    },
    "disney_magical": {
        "bg_top": (10, 25, 60),
        "bg_bottom": (30, 80, 140),
        "accent": (100, 220, 255),
        "glow": (180, 100, 255),
        "text": (255, 255, 255),
        "card_bg": (20, 45, 90, 220)
    },
    "cartoon_adventure": {
        "bg_top": (15, 50, 40),
        "bg_bottom": (45, 110, 75),
        "accent": (255, 215, 0),
        "glow": (120, 230, 140),
        "text": (255, 255, 255),
        "card_bg": (25, 60, 50, 220)
    }
}

def synthesize_soundtrack(output_wav_path: str, duration_sec: float = 10.0):
    """Synthesize harmonic Pixar/Disney style background soundtrack."""
    sr = 44100
    t = np.linspace(0, duration_sec, int(sr * duration_sec), endpoint=False)
    
    # C Major harmonic chords (C4, E4, G4, C5)
    wave = 0.22 * np.sin(2 * np.pi * 261.63 * t + 0.04 * np.sin(2 * np.pi * 3 * t))
    wave += 0.18 * np.sin(2 * np.pi * 329.63 * t)
    wave += 0.18 * np.sin(2 * np.pi * 392.00 * t)
    wave += 0.12 * np.sin(2 * np.pi * 523.25 * t)
    
    # Sparkle flute harmonics
    sparkle = 0.08 * np.sin(2 * np.pi * 783.99 * t) * np.sin(2 * np.pi * 2 * t)
    wave += sparkle
    
    fade_in = int(sr * 1.0)
    fade_out = int(sr * 1.5)
    env = np.ones_like(wave)
    if len(env) > fade_in + fade_out:
        env[:fade_in] = np.linspace(0, 1, fade_in)
        env[-fade_out:] = np.linspace(1, 0, fade_out)
    
    wave = np.clip(wave * env, -1.0, 1.0)
    audio_int16 = (wave * 32767).astype(np.int16)
    wavfile.write(output_wav_path, sr, audio_int16)
    return output_wav_path

async def synthesize_voiceover(text: str, output_mp3_path: str, voice: str = "hi-IN-MadhurNeural"):
    """Generate neural character speech via Edge-TTS."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=voice, rate="+10%", pitch="+12Hz")
    await communicate.save(output_mp3_path)
    return output_mp3_path

def get_media_duration(file_path: str) -> float:
    """Extract audio duration quickly using ffmpeg."""
    cmd = [
        FFMPEG_EXE, "-i", file_path,
        "-f", "null", "-"
    ]
    res = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    # Parse Duration: 00:00:08.52
    for line in res.stderr.splitlines():
        if "Duration:" in line:
            parts = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return 8.0

def render_3d_pixar_frame(
    output_png_path: str,
    title: str,
    subtitle: str,
    character_name: str = "Chhotu & Didi",
    scene_tag: str = "Scene 1: The Magic Discovery",
    palette_key: str = "pixar_warm",
    width: int = 1920,
    height: int = 1080
):
    """Render high-resolution 1080p 3D Pixar scene layout (16:9, or 9:16 when height > width)."""
    palette = PALETTES.get(palette_key, PALETTES["pixar_warm"])
    if height > width:
        return _render_vertical_frame(output_png_path, title, subtitle, character_name, scene_tag,
                                      palette, width, height)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    
    # Background Gradient
    top_c = palette["bg_top"]
    bot_c = palette["bg_bottom"]
    for y in range(height):
        ratio = y / float(height)
        r = int(top_c[0] * (1 - ratio) + bot_c[0] * ratio)
        g = int(top_c[1] * (1 - ratio) + bot_c[1] * ratio)
        b = int(top_c[2] * (1 - ratio) + bot_c[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))
        
    glow = palette["glow"]
    accent = palette["accent"]
    
    # 3D Lighting orbs
    for rad in range(300, 30, -20):
        alpha = int(30 * (1.0 - rad / 300.0))
        draw.ellipse([(width - 350 - rad, 120 - rad), (width - 350 + rad, 120 + rad)], fill=(glow[0], glow[1], glow[2], alpha))
        
    for rad in range(250, 30, -20):
        alpha = int(25 * (1.0 - rad / 250.0))
        draw.ellipse([(250 - rad, height - 200 - rad), (250 + rad, height - 200 + rad)], fill=(accent[0], accent[1], accent[2], alpha))
        
    # Badges
    draw.rounded_rectangle([(80, 50), (450, 110)], radius=14, fill=(15, 20, 35, 230), outline=accent, width=2)
    draw.text((105, 68), "✨ 3D PIXAR ANIMATION ENGINE", fill=accent)
    
    draw.rounded_rectangle([(width - 480, 50), (width - 80, 110)], radius=14, fill=(15, 20, 35, 230), outline=(200, 220, 255), width=1)
    draw.text((width - 450, 68), f"🎬 {scene_tag}", fill=(220, 235, 255))
    
    # Central Character Showcase Card
    card_box = [(width // 2 - 580, 170), (width // 2 + 580, height - 220)]
    draw.rounded_rectangle(card_box, radius=24, fill=palette["card_bg"], outline=accent, width=3)
    
    avatar = (width // 2, 330)
    draw.ellipse([(avatar[0] - 90, avatar[1] - 90), (avatar[0] + 90, avatar[1] + 90)], fill=(30, 15, 55), outline=accent, width=4)
    draw.text((avatar[0] - 40, avatar[1] - 35), "🎨", font_size=70)
    
    draw.text((width // 2 - 130, 460), f"🎭 {character_name}", fill=accent, font_size=30)
    draw.text((width // 2 - 460, 520), title[:60], fill=(255, 255, 255), font_size=38)
    
    # Subtitle dialogue bubble
    bubble = [(width // 2 - 500, 600), (width // 2 + 500, 720)]
    draw.rounded_rectangle(bubble, radius=16, fill=(12, 18, 36, 230), outline=(110, 130, 180), width=2)
    draw.text((width // 2 - 470, 635), f'"{subtitle}"', fill=(240, 245, 255), font_size=26)
    
    # Bottom Strip
    draw.rounded_rectangle([(80, height - 160), (width - 80, height - 70)], radius=16, fill=(15, 20, 35, 240), outline=(80, 110, 160), width=2)
    draw.text((110, height - 125), "⚡ 100% UNRESTRICTED HYBRID PIPELINE  •  LOCAL TTS + SYNTHETICS + FFMPEG COMPOSITING", fill=(180, 205, 245), font_size=20)
    draw.text((width - 390, height - 125), "0 CANVA AI CREDITS ✓", fill=(100, 255, 160), font_size=20)

    img.convert("RGB").save(output_png_path, "PNG")
    return output_png_path

def _render_vertical_frame(output_png_path, title, subtitle, character_name, scene_tag, palette, width, height):
    """9:16 reel layout: badges on top, character card in the middle, subtitle bubble below.
    Text is wrapped to the narrow width and kept clear of the reel UI at the bottom."""
    from thumbnail_engine import _font, _latin, _wrap
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    top_c, bot_c = palette["bg_top"], palette["bg_bottom"]
    for y in range(height):
        r = y / float(height)
        draw.line([(0, y), (width, y)], fill=tuple(int(top_c[i] * (1 - r) + bot_c[i] * r) for i in range(3)) + (255,))
    glow, accent = palette["glow"], palette["accent"]
    for rad in range(420, 30, -20):
        a = int(30 * (1.0 - rad / 420.0))
        draw.ellipse([(width - 200 - rad, 260 - rad), (width - 200 + rad, 260 + rad)], fill=glow + (a,))
    for rad in range(360, 30, -20):
        a = int(25 * (1.0 - rad / 360.0))
        draw.ellipse([(160 - rad, height - 420 - rad), (160 + rad, height - 420 + rad)], fill=accent + (a,))

    m = 70
    badge_f = _font(30)
    draw.rounded_rectangle([(m, 110), (width - m, 190)], radius=18, fill=(15, 20, 35, 230), outline=accent, width=3)
    draw.text((width // 2, 150), "3D PIXAR ANIMATION", font=badge_f, fill=accent, anchor="mm")
    draw.text((width // 2, 240), scene_tag.upper(), font=_font(28), fill=(220, 235, 255), anchor="mm")

    card = [(m, 320), (width - m, 1280)]
    draw.rounded_rectangle(card, radius=32, fill=palette["card_bg"], outline=accent, width=4)
    cx, cy = width // 2, 560
    draw.ellipse([(cx - 170, cy - 170), (cx + 170, cy + 170)], fill=(30, 15, 55), outline=accent, width=6)
    draw.ellipse([(cx - 120, cy - 120), (cx + 120, cy + 120)], fill=glow + (120,))
    if _latin(character_name):
        draw.text((cx, 790), character_name, font=_font(40), fill=accent, anchor="mm")
    text_w = width - 2 * m - 80
    tf = _font(64)
    y = 860
    for line in (_wrap(draw, title, tf, text_w) if _latin(title) else [])[:3]:
        draw.text((cx, y), line, font=tf, fill=(255, 255, 255), anchor="ma", stroke_width=3, stroke_fill=(0, 0, 0))
        y += 78

    if subtitle and _latin(subtitle):
        sf = _font(42)
        lines = _wrap(draw, f'"{subtitle}"', sf, text_w)[:3]
        bh = 60 + 56 * len(lines)
        draw.rounded_rectangle([(m, 1340), (width - m, 1340 + bh)], radius=24, fill=(12, 18, 36, 230),
                               outline=(110, 130, 180), width=3)
        y = 1370
        for line in lines:
            draw.text((cx, y), line, font=sf, fill=(240, 245, 255), anchor="ma")
            y += 56
    img.convert("RGB").save(output_png_path, "PNG")
    return output_png_path

VIDEO_SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080)}

GEMINI_SCRIPT_MODELS = os.environ.get(
    "GEMINI_SCRIPT_MODELS", "gemini-3.6-flash,gemini-flash-lite-latest,gemini-2.5-flash").split(",")
MOOD_PALETTES = {"tech": "disney_magical", "mystery": "disney_magical", "inspiring": "disney_magical",
                 "happy": "cartoon_adventure", "festive": "cartoon_adventure", "adventure": "cartoon_adventure"}

_TOPIC_RE = re.compile(r"\b(?:about|of|on|showing|where|that)\s+(.+)", re.IGNORECASE)

def _topic_from_prompt(prompt: str) -> str:
    """'Make a 3D Pixar video about AI agents fixing an outage.' -> 'AI agents fixing an outage'"""
    m = _TOPIC_RE.search(prompt or "")
    topic = (m.group(1) if m else prompt or "").strip().rstrip(".!?")
    topic = re.sub(r"\s+with\s+(hindi|english)\s+voice-?overs?$", "", topic, flags=re.IGNORECASE)
    return topic[:90] or "a new idea"

# Offline puzzle hooks: (thumbnail question, answer) -- the narration asks it and reveals it last.
_TEMPLATE_PUZZLES = {
    "tech": ("Can AI fix it ALONE?", "No. The real magic is AI and people working together."),
    "mystery": ("Who broke it? Can you guess?", "It was a single forgotten setting. One tiny change fixed everything."),
    "emotional": ("Will they ever meet again?", "Yes. Distance changes places, never hearts."),
    "inspiring": ("Can ONE step change everything?", "Yes. The first step is the one that changes everything."),
}
_DEFAULT_PUZZLE = ("What happens next? Can you guess?", "Every big change starts with one small step.")

def _template_script(prompt: str, language: str, mood: str) -> dict:
    """Offline fallback narration built from the prompt's topic, with a puzzle hook."""
    topic = _topic_from_prompt(prompt)
    question, answer = _TEMPLATE_PUZZLES.get(mood, _DEFAULT_PUZZLE)
    if language == "hi":
        # An English topic read by the Hindi voice sounds broken, so only Hindi topics are spoken.
        about = "" if topic.isascii() else f"आज की कहानी है {topic} की। "
        lines = {
            "emotional": f"क्या वो फिर कभी मिलेंगे? {about}कुछ रिश्ते शब्दों से नहीं, दिल से बनते हैं। और जवाब है: हाँ, दूरियाँ जगह बदलती हैं, दिल नहीं।",
            "tech": f"क्या AI अकेले यह कर सकता है? {about}स्मार्ट सोच, तेज़ काम। और जवाब है: नहीं, असली जादू इंसान और AI के साथ काम करने में है।",
        }
        narration = lines.get(mood, f"सोचिए, आगे क्या होगा? {about}हर बड़ा बदलाव एक छोटे कदम से शुरू होता है। और यही है जवाब।")
        subtitle = "Ek chhoti si kahani, ek bada sapna."
    else:
        lines = {
            "tech": f"{question} Picture this: {topic}. Smart tools, sharp minds, and one team that never stops learning. The answer? {answer}",
            "mystery": f"{question} Something didn't add up: {topic}. One by one, the clues fell into place. The answer? {answer}",
            "emotional": f"{question} This is a story about {topic}, and the people who make life feel like home. The answer? {answer}",
            "inspiring": f"{question} This is a story about {topic}, and what happens when you decide to grow. The answer? {answer}",
        }
        narration = lines.get(mood, f"{question} Here's a story about {topic}. The answer? {answer}")
        subtitle = topic[:70]
    return {"title": topic[:1].upper() + topic[1:50], "narration": narration, "subtitle": subtitle,
            "characters": "Chhotu & Didi" if language == "hi" else "Leo & Maya",
            "hook_question": question, "answer": answer, "source": "template"}

def _gemini_script(prompt: str, language: str, mood: str):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    from google import genai
    from google.genai import types
    lang = ("Hindi in Devanagari script (simple spoken Hindi, English tech words are fine)"
            if language == "hi" else "English")
    instruction = (
        "You write a short animated video built around a curiosity-gap puzzle: the thumbnail asks a "
        "question or riddle, and viewers only learn the answer by watching to the end.\n"
        f"Video request: {prompt}\nMood: {mood}\n"
        "Return JSON with exactly these keys:\n"
        "The puzzle MUST come from the real subject of the request: find the single most "
        "curiosity-provoking question about that exact topic (a surprising fact, a 'how/why', a "
        "'what happens when', or a guess-the-outcome). For IT, AI, cloud, data or career topics the "
        "answer must be true and teach something real (e.g. a real cause, technique, number or "
        "outcome). For personal or emotional stories the answer is the story's real turning point. "
        "Never invent silly or unrelated twists.\n"
        '  "hook_question": the thumbnail puzzle in English, max 55 characters, ends with "?", '
        "names the topic, must NOT reveal or hint the answer\n"
        '  "answer": the reveal in English, max 90 characters, directly answers hook_question\n'
        '  "title": English title, max 45 characters, must not give away the answer\n'
        f'  "spoken_question": exactly the hook_question, translated into {lang}\n'
        f'  "story": 25-40 words in {lang}, warm storyteller voice, no emojis, no hashtags; builds '
        "suspense toward the answer but must NOT state or hint it\n"
        f'  "spoken_answer": one sentence in {lang} that reveals exactly the same answer as "answer"\n'
        '  "subtitle": one short on-screen line in Latin script (Hinglish if Hindi), max 70 characters, no spoilers\n'
        '  "characters": names of 1-2 characters, Latin script, max 25 characters'
    )
    client = genai.Client(api_key=api_key)  # keep a reference: a temporary client is closed mid-request
    # Free tier returns 503/429 under load: try each model in the chain, then one more round.
    attempts = [m for _ in range(2) for m in GEMINI_SCRIPT_MODELS]
    for i, model in enumerate(attempts):
        try:
            resp = client.models.generate_content(
                model=model, contents=instruction,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.7))
            break
        except Exception as e:
            if i == len(attempts) - 1 or not any(c in str(e) for c in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
                raise
            if model == GEMINI_SCRIPT_MODELS[-1]:
                time.sleep(2)
    data = json.loads(resp.text)
    if isinstance(data, list) and data:
        data = data[0]
    for k, v in list(data.items()):
        if isinstance(v, list):  # e.g. "characters": ["Byte", "Ravi"]
            data[k] = " & ".join(str(x.get("name", x)) if isinstance(x, dict) else str(x) for x in v)
    if not all(isinstance(data.get(k), str) and data[k].strip()
               for k in ("title", "subtitle", "characters", "hook_question", "answer",
                         "spoken_question", "story", "spoken_answer")):
        return None
    # Assemble question -> story -> reveal ourselves so the spoken puzzle and answer always
    # match the thumbnail.
    story = " ".join(data["story"].split()[:55])
    data["narration"] = f"{data['spoken_question'].strip()} {story} {data['spoken_answer'].strip()}"
    data["hook_question"], data["answer"] = data["hook_question"][:70], data["answer"][:120]
    data["title"], data["subtitle"], data["characters"] = data["title"][:50], data["subtitle"][:80], data["characters"][:30]
    data["source"] = "gemini"
    return data

async def write_story_script(prompt: str, language: str, mood: str) -> dict:
    """Narration that follows the prompt: Gemini (free tier) if GEMINI_API_KEY is set,
    otherwise an offline template. Returns {title, narration, subtitle, characters, source}."""
    try:
        loop = asyncio.get_running_loop()
        script = await asyncio.wait_for(loop.run_in_executor(None, _gemini_script, prompt, language, mood), 35)
        if script:
            return script
    except Exception as e:
        print(f"[hybrid_video_engine] Gemini script failed, using template: {type(e).__name__} {e}")
    return _template_script(prompt, language, mood)

async def render_hybrid_video(
    story_prompt: str,
    output_mp4_path: str,
    character_name: str = "Chhotu & Didi",
    language: str = "hi",
    music_info: dict = None,
    script_info: dict = None,
    aspect: str = None
):
    """Ultra-fast, high-definition 3D Pixar animated video compositor.
    `aspect` is "9:16" (reel, default via STUDIO_VIDEO_ASPECT) or "16:9"; the outro and end
    card are picked to match. If `music_info` / `script_info` are dicts they are filled with
    the soundtrack details (mood, source, credit) and the narration (title, narration, source)."""
    aspect = aspect or os.environ.get("STUDIO_VIDEO_ASPECT", "9:16")
    width, height = VIDEO_SIZES.get(aspect, VIDEO_SIZES["9:16"])
    from music_engine import build_soundtrack, detect_mood, mix_filter
    work_dir = os.path.dirname(os.path.abspath(output_mp4_path))
    os.makedirs(work_dir, exist_ok=True)
    temp_prefix = os.path.join(work_dir, f"tmp_{os.path.basename(output_mp4_path).replace('.mp4', '')}")

    frame_path = f"{temp_prefix}_frame.png"
    voice_path = f"{temp_prefix}_voice.mp3"
    music_path = f"{temp_prefix}_music.wav"

    # 1. Voiceover script that follows the prompt
    if language == "hi" or any(k in story_prompt.lower() for k in ["hindi", "chhotu", "didi", "bhai", "behan"]):
        language, voice = "hi", "hi-IN-MadhurNeural"
    else:
        language, voice = "en", "en-US-ChristopherNeural"
    mood = detect_mood(story_prompt)
    script = await write_story_script(story_prompt, language, mood)
    if script_info is not None:
        script_info.update(script)
        script_info["size"] = (width, height)

    # 2. Render 1080p Visual Scene
    render_3d_pixar_frame(
        output_png_path=frame_path,
        title=script["title"],
        subtitle=script["subtitle"],
        character_name=f"{script['characters']} (3D Pixar)",
        scene_tag=f"Scene 1: {mood.title()}",
        palette_key=MOOD_PALETTES.get(mood, "pixar_warm"),
        width=width,
        height=height
    )

    # 3. Synthesize Edge-TTS Speech
    await synthesize_voiceover(text=script["narration"], output_mp3_path=voice_path, voice=voice)
    
    # 4. Measure Exact Audio Duration
    duration = get_media_duration(voice_path)
    if duration < 3.0: duration = 6.0
    
    # 5. Mood-matched soundtrack (library track / FluidSynth / synth) with a
    #    music-only lead-in before the voice and a tail after it.
    lead_in, tail = 1.0, 2.0
    total = duration + lead_in + tail
    info = build_soundtrack(story_prompt, total, music_path, mood=mood)
    if music_info is not None:
        music_info.update(info)

    # 6. Ultra-Fast High-Definition FFmpeg Motion Compositing
    #    (music ducked under the voice, final mix normalised to -14 LUFS)
    duration = total
    cmd = [
        FFMPEG_EXE, "-y",
        "-loop", "1", "-t", f"{duration:.2f}", "-i", frame_path,
        "-i", voice_path,
        "-i", music_path,
        "-filter_complex",
        f"[0:v]zoompan=z='min(zoom+0.0008,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=25[v];"
        + mix_filter(voice_input=1, music_input=2, voice_delay=lead_in, total_sec=total),
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{duration:.2f}",
        output_mp4_path
    ]
    
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # 7. Studio outro clip (16:9 or 9:16 picked by the video's shape).
    if os.environ.get("STUDIO_VIDEO_OUTRO", "1") != "0":
        try:
            from thumbnail_engine import append_outro
            if append_outro(output_mp4_path, output_mp4_path) and script_info is not None:
                script_info["outro"] = True
        except Exception as e:
            print(f"[hybrid_video_engine] outro skipped: {e}")

    # 7b. Like / follow / share end card held on the last frames.
    if os.environ.get("STUDIO_VIDEO_ENDCARD", "1") != "0":
        try:
            from thumbnail_engine import append_endcard
            if append_endcard(output_mp4_path, output_mp4_path,
                              seconds=float(os.environ.get("STUDIO_ENDCARD_SECONDS", "4"))) and script_info is not None:
                script_info["endcard"] = True
        except Exception as e:
            print(f"[hybrid_video_engine] end card skipped: {e}")

    # 8. Puzzle-hook thumbnail (off by default: thumbnails are made separately in ChatGPT;
    #    set STUDIO_VIDEO_THUMBNAIL=1 to turn it back on). Saved as <video>_thumb.jpg, embedded
    #    as cover art and laid over the opening seconds while the narrator asks the puzzle
    #    question. Any failure leaves the video as it is.
    if os.environ.get("STUDIO_VIDEO_THUMBNAIL", "0") != "0":
        try:
            from thumbnail_engine import make_thumbnail, prepend_thumbnail
            thumb_path = os.path.splitext(output_mp4_path)[0] + "_thumb.jpg"
            make_thumbnail(script["title"], thumb_path, mood=mood, background=frame_path,
                           question=script.get("hook_question"), teaser=script.get("subtitle"),
                           size=(width, height))
            prepend_thumbnail(output_mp4_path, thumb_path, output_mp4_path,
                              seconds=float(os.environ.get("STUDIO_THUMBNAIL_SECONDS", "2.5")),
                              overlay=True)
            if script_info is not None:
                script_info["thumbnail"] = thumb_path
        except Exception as e:
            print(f"[hybrid_video_engine] thumbnail skipped: {e}")

    # Cleanup temp files
    for p in [frame_path, voice_path, music_path]:
        if os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
            
    return output_mp4_path

if __name__ == "__main__":
    out_file = "/mnt/c/Users/keysh/github/ai-orchestration/Hybrid_Pixar_Demo_1080p.mp4"
    print("🚀 Rendering 3D Pixar video...")
    asyncio.run(render_hybrid_video("Pixar story", out_file))
    print(f"✓ Video ready: {out_file} (Size: {os.path.getsize(out_file)} bytes)")
