import os
import subprocess
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
import imageio_ffmpeg

from hybrid_video_engine import synthesize_soundtrack, get_media_duration

load_dotenv()

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

DEFAULT_MODEL = "Wan-AI/Wan2.2-TI2V-5B"


def generate_raw_clip(prompt: str, output_mp4_path: str, model: str = DEFAULT_MODEL, num_frames: int | None = None) -> str:
    """Call HF Inference Providers text-to-video and save the raw clip (no audio)."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN not set in environment/.env")

    client = InferenceClient(api_key=token)
    kwargs = {}
    if num_frames:
        kwargs["num_frames"] = num_frames

    video_bytes = client.text_to_video(prompt, model=model, **kwargs)

    os.makedirs(os.path.dirname(os.path.abspath(output_mp4_path)), exist_ok=True)
    with open(output_mp4_path, "wb") as f:
        f.write(video_bytes)
    return output_mp4_path


def generate_video_with_music(prompt: str, output_mp4_path: str, model: str = DEFAULT_MODEL, num_frames: int | None = None) -> str:
    """Generate an AI text-to-video clip and mix in a soothing synthesized background pad."""
    work_dir = os.path.dirname(os.path.abspath(output_mp4_path)) or "."
    os.makedirs(work_dir, exist_ok=True)
    temp_prefix = os.path.join(work_dir, f"tmp_hf_{os.path.basename(output_mp4_path).replace('.mp4', '')}")
    raw_path = f"{temp_prefix}_raw.mp4"
    music_path = f"{temp_prefix}_music.wav"

    generate_raw_clip(prompt, raw_path, model=model, num_frames=num_frames)

    duration = get_media_duration(raw_path)
    if duration < 1.0:
        duration = 5.0

    synthesize_soundtrack(output_wav_path=music_path, duration_sec=duration + 0.5)

    cmd = [
        FFMPEG_EXE, "-y",
        "-i", raw_path,
        "-i", music_path,
        "-filter_complex", "[1:a]volume=0.35[bg]",
        "-map", "0:v",
        "-map", "[bg]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_mp4_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    for p in [raw_path, music_path]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    return output_mp4_path


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Gurukul_Student_Test.mp4")
    print("Generating test clip...")
    generate_raw_clip(
        "A young student sitting under a large banyan tree in an ancient Indian gurukul, "
        "reading palm-leaf manuscripts, warm golden sunlight filtering through leaves, "
        "peaceful serene atmosphere, cinematic 3D animated style",
        out
    )
    print(f"Done: {out} ({os.path.getsize(out)} bytes)")
