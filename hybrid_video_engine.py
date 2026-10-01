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
    """Render high-resolution 1080p 3D Pixar scene layout."""
    palette = PALETTES.get(palette_key, PALETTES["pixar_warm"])
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

def _template_script(prompt: str, language: str, mood: str) -> dict:
    """Offline fallback narration built from the prompt's topic."""
    topic = _topic_from_prompt(prompt)
    if language == "hi":
        # An English topic read by the Hindi voice sounds broken, so only Hindi topics are spoken.
        about = "" if topic.isascii() else f"आज की कहानी है {topic} की। "
        lines = {
            "emotional": f"कुछ रिश्ते शब्दों से नहीं, दिल से बनते हैं। {about}देखिए, और उन लम्हों को याद कीजिए जो हमेशा साथ रहते हैं।",
            "tech": f"सोचिए, अगर मशीनें भी हमारी टीम का हिस्सा हों! {about}स्मार्ट सोच, तेज़ काम, और एक नया भविष्य।",
        }
        narration = lines.get(mood, f"आइए, एक छोटी सी कहानी देखते हैं। {about}हर बड़ा बदलाव एक छोटे कदम से शुरू होता है।")
        subtitle = "Ek chhoti si kahani, ek bada sapna."
    else:
        lines = {
            "tech": f"Picture this: {topic}. Smart tools, sharp minds, and one team that never stops learning. The future isn't coming, it's already here.",
            "mystery": f"Something didn't add up. The clue? {topic}. One by one, the pieces fell into place, until the answer was clear.",
            "emotional": f"Some moments stay with us forever. This is a story about {topic}, and the people who make life feel like home.",
            "inspiring": f"Every big change starts with one brave step. This is a story about {topic}, and what happens when you decide to grow.",
        }
        narration = lines.get(mood, f"Once upon a time, there was a story about {topic}. Let's see where it takes us!")
        subtitle = topic[:70]
    return {"title": topic[:1].upper() + topic[1:50],"narration": narration, "subtitle": subtitle,
            "characters": "Chhotu & Didi" if language == "hi" else "Leo & Maya", "source": "template"}

def _gemini_script(prompt: str, language: str, mood: str):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    from google import genai
    from google.genai import types
    lang = ("Hindi in Devanagari script (simple spoken Hindi, English tech words are fine)"
            if language == "hi" else "English")
    instruction = (
        "You write narration for a 10-15 second animated short video.\n"
        f"Video request: {prompt}\nMood: {mood}\n"
        "Return JSON with exactly these keys:\n"
        '  "title": English title, max 45 characters\n'
        f'  "narration": 25-40 words in {lang}, warm storyteller voice, no emojis, no hashtags\n'
        '  "subtitle": one short on-screen line in Latin script (Hinglish if Hindi), max 70 characters\n'
        '  "characters": names of 1-2 characters, Latin script, max 25 characters'
    )
    client = genai.Client(api_key=api_key)  # keep a reference: a temporary client is closed mid-request
    # Free tier returns 503/429 under load: try each model in the chain, then one more round.
    attempts = [m for _ in range(2) for m in GEMINI_SCRIPT_MODELS]
    for i, model in enumerate(attempts):
        try:
            resp = client.models.generate_content(
                model=model, contents=instruction,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.9))
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
    if not all(isinstance(data.get(k), str) and data[k].strip() for k in ("title", "narration", "subtitle", "characters")):
        return None
    words = data["narration"].split()
    data["narration"] = " ".join(words[:60])
    data["title"], data["subtitle"], data["characters"] = data["title"][:50], data["subtitle"][:80], data["characters"][:30]
    data["source"] = "gemini"
    return data

async def write_story_script(prompt: str, language: str, mood: str) -> dict:
    """Narration that follows the prompt: Gemini (free tier) if GEMINI_API_KEY is set,
    otherwise an offline template. Returns {title, narration, subtitle, characters, source}."""
    try:
        loop = asyncio.get_running_loop()
        script = await asyncio.wait_for(loop.run_in_executor(None, _gemini_script, prompt, language, mood), 20)
        if script:
            return script
    except Exception as e:
        print(f"[hybrid_video_engine] Gemini script failed, using template: {e}")
    return _template_script(prompt, language, mood)

async def render_hybrid_video(
    story_prompt: str,
    output_mp4_path: str,
    character_name: str = "Chhotu & Didi",
    language: str = "hi",
    music_info: dict = None,
    script_info: dict = None
):
    """Ultra-fast, high-definition 3D Pixar animated video compositor.
    If `music_info` / `script_info` are dicts they are filled with the soundtrack
    details (mood, source, credit) and the narration (title, narration, source)."""
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

    # 2. Render 1080p Visual Scene
    render_3d_pixar_frame(
        output_png_path=frame_path,
        title=script["title"],
        subtitle=script["subtitle"],
        character_name=f"{script['characters']} (3D Pixar)",
        scene_tag=f"Scene 1: {mood.title()}",
        palette_key=MOOD_PALETTES.get(mood, "pixar_warm")
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
        f"[0:v]zoompan=z='min(zoom+0.0008,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=25[v];"
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
