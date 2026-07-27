import json
import re
import shutil
import subprocess
from pathlib import Path

import config
import db

FFMPEG_TIMEOUT = 6 * 60 * 60
DURATION_TOLERANCE_SECONDS = 2.0
DURATION_TOLERANCE_FRACTION = 0.02


def route_folder(is_foreign, resolution):
    if is_foreign:
        return "Foreign"
    if resolution == "2160p":
        return "4K"
    return "1080p"


def _ffprobe_duration(path):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    return float(json.loads(result.stdout)["format"]["duration"])


def _verify_duration(source_duration, output_path):
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"output missing or empty: {output_path}")
    if source_duration is None:
        return
    try:
        output_duration = _ffprobe_duration(output_path)
    except Exception:
        return
    tolerance = max(DURATION_TOLERANCE_SECONDS, source_duration * DURATION_TOLERANCE_FRACTION)
    if abs(output_duration - source_duration) > tolerance:
        raise RuntimeError(
            f"output duration {output_duration:.1f}s does not match source {source_duration:.1f}s"
        )


def _cleanup_extra_files(working_folder, keep_path):
    for entry in working_folder.iterdir():
        if entry == keep_path:
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _move_to_destination(conn, movie_id, movie, working_folder, folder_name):
    dest_folder_name = route_folder(movie["is_foreign"], movie["resolution"])
    destination = Path(config.TORRENTS_ROOT) / dest_folder_name / folder_name

    if working_folder.parent == destination.parent:
        return working_folder

    if destination.exists():
        conn.execute(
            "UPDATE movies SET collision = 1, updated_at = datetime('now') WHERE id = ?",
            (movie_id,),
        )
        conn.commit()
        raise RuntimeError(f"destination already exists: {destination}")

    shutil.move(str(working_folder), str(destination))
    return destination


def _prepare_working_folder(movie, folder_name):
    """Rename/relocate the raw discovered item in-place to the IMDb-named
    folder, within its current parent directory. Returns (working_folder,
    source_file_path)."""
    original_path = Path(movie["original_path"])

    if original_path.is_dir():
        working_folder = original_path.parent / folder_name
        if working_folder != original_path:
            original_path.rename(working_folder)
        source_file = next(
            f for f in sorted(working_folder.iterdir())
            if f.suffix.lower() in (".mkv", ".mp4")
        )
        return working_folder, source_file

    working_folder = original_path.parent / folder_name
    working_folder.mkdir(exist_ok=True)
    source_file = working_folder / original_path.name
    original_path.rename(source_file)
    return working_folder, source_file


def _mkvmerge_duration(path):
    """Duration in seconds via mkvmerge -J, or None if it can't be determined."""
    try:
        cmd = ["mkvmerge", "-J", str(path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        duration_ns = json.loads(result.stdout).get("container", {}).get("properties", {}).get("duration")
        return duration_ns / 1_000_000_000 if duration_ns else None
    except Exception:
        return None


def list_bdmv_playlists(original_path):
    """Enumerate .mpls playlists under BDMV/PLAYLIST, longest first, with a
    best-effort duration (mkvmerge -J) and a size estimate prorated from the
    total size of BDMV/STREAM by each playlist's share of total duration."""
    p = Path(original_path)
    playlist_dir = p / "BDMV" / "PLAYLIST"
    stream_dir = p / "BDMV" / "STREAM"
    if not playlist_dir.is_dir():
        return []

    total_stream_bytes = sum(f.stat().st_size for f in stream_dir.glob("*.m2ts")) if stream_dir.is_dir() else 0

    playlists = []
    for mpls in sorted(playlist_dir.glob("*.mpls")):
        playlists.append({"filename": mpls.name, "duration_seconds": _mkvmerge_duration(mpls)})

    total_duration = sum(pl["duration_seconds"] or 0 for pl in playlists)
    for pl in playlists:
        if total_duration and pl["duration_seconds"]:
            pl["estimated_bytes"] = int(total_stream_bytes * (pl["duration_seconds"] / total_duration))
        else:
            pl["estimated_bytes"] = None

    playlists.sort(key=lambda pl: pl["duration_seconds"] or 0, reverse=True)
    return playlists


def format_duration(seconds):
    if not seconds:
        return "unknown"
    total_minutes = int(seconds // 60)
    h, m = divmod(total_minutes, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def format_size(num_bytes):
    if not num_bytes:
        return "unknown"
    gb = num_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{num_bytes / (1024 ** 2):.0f} MB"


def _prepare_bdmv_working_folder(movie, folder_name):
    original_path = Path(movie["original_path"])
    working_folder = original_path.parent / folder_name
    if working_folder != original_path:
        original_path.rename(working_folder)
    return working_folder


def _run_mkvmerge_remux(source, dest, progress):
    cmd = ["mkvmerge", "--gui-mode", str(source), "-o", str(dest)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    pattern = re.compile(r"#GUI#progress (\d+)%")
    for line in proc.stdout:
        m = pattern.search(line)
        if m:
            pct = 20 + min(65, int(m.group(1)) * 0.65)
            progress(pct, "remuxing")
    proc.wait(timeout=FFMPEG_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"mkvmerge exited with code {proc.returncode}")


def preprocess_bdmv(movie_id, progress, playlist_filename):
    conn = db.connect()
    try:
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            raise RuntimeError(f"movie {movie_id} not found")

        folder_name = f"{movie['clean_title']} ({movie['year']})"

        progress(5, "preparing working folder")
        working_folder = _prepare_bdmv_working_folder(movie, folder_name)
        mpls_path = working_folder / "BDMV" / "PLAYLIST" / playlist_filename
        output_path = working_folder / f"{folder_name}.mkv"

        progress(10, "probing playlist duration")
        duration = _mkvmerge_duration(mpls_path)

        progress(20, "remuxing BDMV playlist")
        _run_mkvmerge_remux(mpls_path, output_path, progress)

        progress(85, "verifying output")
        _verify_duration(duration, output_path)

        progress(88, "deleting BDMV source")
        bdmv_dir = working_folder / "BDMV"
        if bdmv_dir.is_dir():
            shutil.rmtree(bdmv_dir)

        progress(90, "cleaning up extra files")
        _cleanup_extra_files(working_folder, output_path)

        progress(95, "moving to destination")
        final_path = _move_to_destination(conn, movie_id, movie, working_folder, folder_name)

        conn.execute(
            """
            UPDATE movies SET status = 'preprocessed', final_path = ?, collision = 0,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (str(final_path), movie_id),
        )
        conn.commit()
        progress(100, "done")
    finally:
        conn.close()


def _run_ffmpeg_remux(source, dest, progress, total_duration):
    cmd = [
        "ffmpeg", "-y", "-i", str(source), "-c", "copy", "-map", "0",
        "-progress", "pipe:1", "-nostats", str(dest),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_ms=") and total_duration:
            try:
                out_seconds = int(line.split("=", 1)[1]) / 1_000_000
                pct = 15 + min(70, out_seconds / total_duration * 70)
                progress(pct, "converting")
            except (ValueError, ZeroDivisionError):
                pass
    proc.wait(timeout=FFMPEG_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with code {proc.returncode}")


def preprocess_mp4(movie_id, progress):
    conn = db.connect()
    try:
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            raise RuntimeError(f"movie {movie_id} not found")

        folder_name = f"{movie['clean_title']} ({movie['year']})"

        progress(5, "preparing working folder")
        working_folder, source_file = _prepare_working_folder(movie, folder_name)
        output_path = working_folder / f"{folder_name}.mkv"

        progress(10, "probing source")
        try:
            duration = _ffprobe_duration(source_file)
        except Exception:
            duration = None

        progress(15, "converting to MKV")
        _run_ffmpeg_remux(source_file, output_path, progress, duration)

        progress(85, "verifying output")
        _verify_duration(duration, output_path)

        source_file.unlink()

        progress(90, "cleaning up extra files")
        _cleanup_extra_files(working_folder, output_path)

        progress(95, "moving to destination")
        final_path = _move_to_destination(conn, movie_id, movie, working_folder, folder_name)

        conn.execute(
            """
            UPDATE movies SET status = 'preprocessed', final_path = ?, collision = 0,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (str(final_path), movie_id),
        )
        conn.commit()
        progress(100, "done")
    finally:
        conn.close()
