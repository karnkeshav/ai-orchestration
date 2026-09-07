"""
MCP server exposing Hugging Face Inference Providers text-to-video generation
as a tool, so it can be called directly from Claude Code (or any MCP client)
instead of going through server.py's web UI.

Requires HF_TOKEN in .env (a free Hugging Face token with "Inference Providers"
permission). Free-tier credit is $0.10/month; a few seconds of video will
exhaust it, so keep num_frames small unless you've added paid credit.
"""

import os
from fastmcp import FastMCP

from hf_video_engine import generate_raw_clip, generate_video_with_music, DEFAULT_MODEL

mcp = FastMCP("hf-video")


@mcp.tool
def generate_text_to_video(
    prompt: str,
    output_path: str,
    model: str = DEFAULT_MODEL,
    num_frames: int = 17,
    with_music: bool = True,
) -> str:
    """Generate a short AI video clip from a text prompt via Hugging Face Inference Providers.

    Args:
        prompt: Description of the scene to generate.
        output_path: Absolute path to write the resulting .mp4 to.
        model: HF model id (default Wan-AI/Wan2.2-TI2V-5B).
        num_frames: Frame count requested from the model. Lower = cheaper.
            ~17 frames at 24fps is roughly 0.7s and costs a few cents on the
            free tier; the $0.10/month free credit covers only 1-2 seconds
            of video total.
        with_music: If true, mixes in a synthesized soothing background pad.

    Returns:
        The output_path the video was written to.
    """
    if with_music:
        return generate_video_with_music(prompt, output_path, model=model, num_frames=num_frames)
    return generate_raw_clip(prompt, output_path, model=model, num_frames=num_frames)


if __name__ == "__main__":
    mcp.run()
