"""Local evidence and editable metadata suggestions for a finished clip.

No network request leaves this PC. Model suggestions are drafts, never an
authorization to publish. Missing local capabilities produce a partial result.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
import re
from urllib.error import URLError
from urllib.request import Request, urlopen

OLLAMA = os.environ.get("VIDEO_DROP_OLLAMA_URL", "http://127.0.0.1:11434")
VISION_MODEL = os.environ.get("VIDEO_DROP_VISION_MODEL", "qwen2.5vl:7b")
TEXT_MODEL = os.environ.get("VIDEO_DROP_TEXT_MODEL", "qwen3:14b")
UNSAFE_TAGS = {
    "political", "politics", "politicalcommentary", "election", "elections", "voter", "voters", "voting",
    "swingvoter", "swingvoters", "suburbanlife", "campaign",
    "partisan", "democrat", "republican", "conservative", "liberal", "government",
    "abortion", "immigration", "immigrant", "socialissues", "identitypolitics",
    "religion", "israel", "palestine", "ukraine", "russia", "genocide",
    "genderpolitics", "genderidentity", "extremist", "terrorism", "terrorist",
    "whitepower", "nazi", "hitler", "racist", "racism", "hate",
}


def safe_tag(value: str) -> bool:
    compact = re.sub(r"[^\w]", "", value.casefold())
    return bool(compact) and not any(compact == term or (len(term) >= 6 and term in compact) for term in UNSAFE_TAGS)


def strip_sensitive_hashtags(text: str) -> str:
    return re.sub(r"(?<!\w)#[\w]+", lambda match: match[0] if safe_tag(match[0]) else "", text).strip()


def probe(path: Path) -> dict:
    process = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    data = json.loads(process.stdout)
    video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("The file has no video stream")
    return {
        "duration_seconds": float(data.get("format", {}).get("duration") or video.get("duration") or 0),
        "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
        "fps": video.get("avg_frame_rate", ""),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in data.get("streams", [])),
    }


def frame(path: Path, second: float) -> bytes:
    process = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(max(0, second)),
        "-i", str(path), "-frames:v", "1", "-vf", "scale=1024:-2:force_original_aspect_ratio=decrease",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-",
    ], capture_output=True, timeout=45, check=True)
    if not process.stdout:
        raise ValueError("Could not extract a video frame")
    return process.stdout


def transcribe(path: Path) -> str:
    from faster_whisper import WhisperModel

    with tempfile.TemporaryDirectory(prefix="video-drop-audio-") as directory:
        audio = Path(directory) / "audio.wav"
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vn", "-ac", "1", "-ar", "16000", str(audio),
        ], capture_output=True, timeout=900, check=True)
        model = WhisperModel(os.environ.get("VIDEO_DROP_TRANSCRIPTION_MODEL", "small"),
                             device="cpu", compute_type="int8", local_files_only=True)
        segments, _ = model.transcribe(str(audio), beam_size=5, vad_filter=True,
                                       condition_on_previous_text=False)
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip())[:80000]


def generate(model: str, prompt: str, images: list[bytes] | None = None, timeout: int = 240) -> dict:
    payload = {"model": model, "prompt": prompt, "stream": False, "format": "json", "think": False,
               "options": {"temperature": 0.2, "num_predict": 900, "num_ctx": 8192}}
    if images:
        payload["images"] = [base64.b64encode(image).decode("ascii") for image in images]
        # The stronger text model follows this pass; release vision VRAM first.
        payload["keep_alive"] = 0
    request = Request(f"{OLLAMA}/api/generate", json.dumps(payload).encode(),
                      {"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    return json.loads(result["response"])


def bounded_text(value: object, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def analyze(path: Path) -> dict:
    metadata = probe(path)
    warnings: list[str] = []
    seconds = metadata["duration_seconds"]
    images = []
    for fraction in (0.08, 0.45, 0.82):
        try:
            images.append(frame(path, seconds * fraction if seconds else 0))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            warnings.append(f"frame extraction: {exc}")
            break
    transcript = ""
    if metadata["has_audio"]:
        try:
            transcript = transcribe(path)
        except (ImportError, OSError, ValueError, subprocess.SubprocessError) as exc:
            warnings.append(f"transcription: {exc}")
    visual = {}
    if images:
        try:
            visual = generate(VISION_MODEL,
                "Describe only what the supplied video frames visibly show. Read the exact large, persistent "
                "on-screen series title if present, separately from dialogue subtitles. Identify the game from "
                "gameplay imagery only; a game mentioned in text does not establish which game is shown. "
                "If a specific version is uncertain, use the franchise or an empty string. Do not infer events "
                "between frames. Return JSON with keys summary, game, on_screen_title.", images, 180)
        except (OSError, ValueError, KeyError, URLError) as exc:
            warnings.append(f"local vision model: {exc}")
    evidence = {"metadata": metadata, "transcript": transcript,
                "visual_summary": bounded_text(visual.get("summary"), 2000),
                "on_screen_title": bounded_text(visual.get("on_screen_title"), 500),
                "game": bounded_text(visual.get("game"), 100), "warnings": warnings,
                "models": {"vision": VISION_MODEL, "text": TEXT_MODEL}}
    prompt = (
        "Suggest editable release metadata for this clip using only the evidence JSON below. "
        "Do not invent a person, game, event, outcome, or quote. Do not use political, election, "
        "identity, religion, hate, extremist, or other sensitive public-policy tags. "
        "Use a verified on-screen series title as central context, and distinguish a requested future game "
        "from the game footage actually shown. Prefer named people, games, and the specific clip premise "
        "over generic filler such as gaming culture, media, or game development. Every tag must be grounded "
        "in the evidence. Keep captions concise. YouTube title max 100 chars, tags max 10. "
        "Put one to three relevant hashtags in the YouTube description, grounded in the evidence. "
        "Return JSON keys youtube_title, youtube_description, youtube_tags (array), "
        "instagram_caption, tiktok_caption, game. "
        "These are suggestions for human review, not publication.\nEVIDENCE:\n" +
        json.dumps({"filename": path.name, "transcript": transcript[:12000],
                    "visual_summary": evidence["visual_summary"], "on_screen_title": evidence["on_screen_title"],
                    "game": evidence["game"]}, ensure_ascii=False)
    )
    try:
        draft = generate(TEXT_MODEL, prompt, timeout=240)
    except (OSError, ValueError, KeyError, URLError) as exc:
        warnings.append(f"local text model: {exc}")
        draft = {}
    tags = draft.get("youtube_tags", [])
    if not isinstance(tags, list):
        tags = []
    suggestions = {
        "youtube_title": bounded_text(draft.get("youtube_title"), 100),
        "youtube_description": strip_sensitive_hashtags(bounded_text(draft.get("youtube_description"), 5000)),
        "youtube_tags": [bounded_text(item, 60) for item in tags[:10] if bounded_text(item, 60) and safe_tag(str(item))],
        "instagram_caption": strip_sensitive_hashtags(bounded_text(draft.get("instagram_caption"), 2200)),
        "tiktok_caption": strip_sensitive_hashtags(bounded_text(draft.get("tiktok_caption"), 2200)),
        "game": bounded_text(draft.get("game"), 100) or evidence["game"],
    }
    return {"status": "partial" if warnings else "complete", "evidence": evidence,
            "suggestions": suggestions, "warnings": warnings}
