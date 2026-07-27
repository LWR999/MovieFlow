import os
import shutil
from pathlib import Path

import config
import db

COPY_CHUNK_SIZE = 4 * 1024 * 1024


def _walk(path):
    for root, dirs, files in os.walk(path):
        yield Path(root), dirs, files


def _folder_size(path):
    total = 0
    for root, _, files in _walk(path):
        for fname in files:
            total += (root / fname).stat().st_size
    return total


def _copy_with_progress(source_folder, dest_folder, progress):
    total_bytes = _folder_size(source_folder) or 1
    copied = 0
    last_reported_pct = -1

    for root, _, files in _walk(source_folder):
        rel = root.relative_to(source_folder)
        dest_dir = dest_folder / rel
        dest_dir.mkdir(parents=True, exist_ok=True)
        for fname in files:
            src_file = root / fname
            dst_file = dest_dir / fname
            with open(src_file, "rb") as fsrc, open(dst_file, "wb") as fdst:
                while True:
                    chunk = fsrc.read(COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    fdst.write(chunk)
                    copied += len(chunk)
                    pct = int(copied / total_bytes * 100)
                    if pct != last_reported_pct:
                        last_reported_pct = pct
                        progress(5 + min(85, pct * 0.85), f"copying ({copied}/{total_bytes} bytes)")
            shutil.copystat(src_file, dst_file)


def _verify_copy(source_folder, dest_folder):
    for root, _, files in _walk(source_folder):
        rel = root.relative_to(source_folder)
        for fname in files:
            src_file = root / fname
            dst_file = dest_folder / rel / fname
            if not dst_file.exists():
                raise RuntimeError(f"copy verification failed: missing {dst_file}")
            if dst_file.stat().st_size != src_file.stat().st_size:
                raise RuntimeError(f"copy verification failed: size mismatch for {dst_file}")


def library_move(movie_id, progress):
    conn = db.connect()
    try:
        movie = conn.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        if movie is None:
            raise RuntimeError(f"movie {movie_id} not found")

        source_folder = Path(movie["final_path"])
        if not source_folder.is_dir():
            raise RuntimeError(f"source folder not found: {source_folder}")

        dest_root = config.ENCODES4K if movie["resolution"] == "2160p" else config.ENCODES
        if not dest_root:
            raise RuntimeError("ENCODES/ENCODES4K environment variable is not set")

        folder_name = f"{movie['clean_title']} ({movie['year']})"
        destination = Path(dest_root) / folder_name

        progress(2, "checking destination")
        if destination.exists():
            conn.execute(
                "UPDATE movies SET collision = 1, updated_at = datetime('now') WHERE id = ?",
                (movie_id,),
            )
            conn.commit()
            raise RuntimeError(f"destination already exists: {destination}")

        progress(5, "copying to library")
        try:
            _copy_with_progress(source_folder, destination, progress)
            progress(92, "verifying copy")
            _verify_copy(source_folder, destination)
        except Exception:
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            raise

        progress(96, "deleting source")
        shutil.rmtree(source_folder)

        conn.execute(
            """
            UPDATE movies SET status = 'in_library', final_path = ?, collision = 0,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (str(destination), movie_id),
        )
        conn.commit()
        progress(100, "done")
    finally:
        conn.close()
