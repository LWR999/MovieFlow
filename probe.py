import json
import os
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4"}
TIMEOUT = 30

LANGUAGE_NAMES = {
    "eng": "English", "rus": "Russian", "fre": "French", "fra": "French",
    "ger": "German", "deu": "German", "jpn": "Japanese", "spa": "Spanish",
    "ita": "Italian", "por": "Portuguese", "kor": "Korean", "chi": "Chinese",
    "zho": "Chinese", "dut": "Dutch", "nld": "Dutch", "swe": "Swedish",
    "nor": "Norwegian", "dan": "Danish", "fin": "Finnish", "pol": "Polish",
    "tur": "Turkish", "ara": "Arabic", "hin": "Hindi", "und": "Unknown",
}

CHANNEL_LABELS = {1: "1.0", 2: "2.0", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}

MKVMERGE_CODEC_DISPLAY = {
    "DTS-HD Master Audio": "DTS-HD MA",
    "DTS-HD High Resolution Audio": "DTS-HD HRA",
    "AC-3": "AC3",
    "E-AC-3": "E-AC3",
}

FFPROBE_CODEC_DISPLAY = {
    "aac": "AAC", "ac3": "AC3", "eac3": "E-AC3", "dts": "DTS",
    "truehd": "TrueHD", "flac": "FLAC", "mp3": "MP3", "opus": "Opus",
}


def _resolve_media_file(path, source_type):
    p = Path(path)
    if source_type == "bdmv":
        stream_dir = p / "BDMV" / "STREAM"
        if not stream_dir.is_dir():
            return None
        candidates = sorted(stream_dir.glob("*.m2ts"), key=lambda f: f.stat().st_size, reverse=True)
        return candidates[0] if candidates else None
    if p.is_file():
        return p
    if p.is_dir():
        for root, _, filenames in os.walk(p):
            for fname in sorted(filenames):
                if Path(fname).suffix.lower() in VIDEO_EXTENSIONS:
                    return Path(root) / fname
    return None


def _ffprobe(path):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, check=True)
    return json.loads(result.stdout)


def _mkvmerge_tracks(path):
    try:
        cmd = ["mkvmerge", "-J", str(path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, check=True)
        return json.loads(result.stdout).get("tracks", [])
    except Exception:
        return None


def resolution_label(width, height):
    if not height:
        return None
    if height >= 2000:
        return "2160p"
    if height >= 1000:
        return "1080p"
    if height >= 700:
        return "720p"
    if height >= 470:
        return "480p"
    return f"{width}x{height}"


def hdr_label(video_stream):
    color_transfer = (video_stream.get("color_transfer") or "").lower()
    side_data = video_stream.get("side_data_list") or []
    side_data_types = " ".join(sd.get("side_data_type", "") for sd in side_data).lower()

    if "dovi" in side_data_types or "dolby vision" in side_data_types:
        return "DV"
    if "hdr10+" in side_data_types or "hdr dynamic metadata" in side_data_types:
        return "HDR10+"
    if color_transfer == "smpte2084":
        return "HDR10"
    if color_transfer == "arib-std-b67":
        return "HLG"
    if color_transfer in ("bt709", "bt470bg", "smpte170m", ""):
        return "SDR"
    return None


def _channel_display(channels):
    if not channels:
        return ""
    return CHANNEL_LABELS.get(channels, f"{channels}ch")


def _format_track(lang_code, codec_display, channels, title):
    lang = LANGUAGE_NAMES.get((lang_code or "und").lower(), lang_code or "Unknown")
    channel_display = _channel_display(channels)
    if title and "commentary" in title.lower():
        return f"Commentary ({lang}) {codec_display} {channel_display}".strip()
    return f"{lang} {codec_display} {channel_display}".strip()


def format_ffprobe_audio(streams):
    results = []
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        codec_name = (s.get("codec_name") or "").lower()
        profile = s.get("profile")
        if codec_name == "dts" and profile and profile.upper() != "DTS":
            codec_display = profile
        else:
            codec_display = FFPROBE_CODEC_DISPLAY.get(codec_name, codec_name.upper())
        tags = s.get("tags") or {}
        results.append(_format_track(tags.get("language"), codec_display, s.get("channels"), tags.get("title")))
    return results


def format_mkvmerge_audio(tracks):
    results = []
    for t in tracks:
        if t.get("type") != "audio":
            continue
        props = t.get("properties", {})
        codec = t.get("codec", "")
        codec_display = MKVMERGE_CODEC_DISPLAY.get(codec, codec)
        results.append(_format_track(
            props.get("language"), codec_display, props.get("audio_channels"), props.get("track_name"),
        ))
    return results


def inspect_media(original_path, source_type):
    result = {"resolution": None, "hdr_flavor": None, "audio_summary": None}
    try:
        media_path = _resolve_media_file(original_path, source_type)
        if media_path is None:
            return result

        probe = _ffprobe(media_path)
        streams = probe.get("streams", [])
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)

        if video_stream:
            result["resolution"] = resolution_label(video_stream.get("width"), video_stream.get("height"))
            result["hdr_flavor"] = hdr_label(video_stream)

        if media_path.suffix.lower() == ".mkv":
            tracks = _mkvmerge_tracks(media_path)
            audio_list = format_mkvmerge_audio(tracks) if tracks is not None else format_ffprobe_audio(streams)
        else:
            audio_list = format_ffprobe_audio(streams)

        result["audio_summary"] = ", ".join(a for a in audio_list if a) or None
    except Exception:
        pass
    return result
