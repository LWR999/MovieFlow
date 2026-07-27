import threading

import config
import db

_SEMAPHORES = {
    "preprocess": threading.Semaphore(config.MAX_CONCURRENT_PREPROCESS_JOBS),
    "process": threading.Semaphore(config.MAX_CONCURRENT_PROCESSING_JOBS),
    "library_move": threading.Semaphore(config.MAX_CONCURRENT_LIBRARY_JOBS),
}

_SUBMIT_LOCK = threading.Lock()


class JobAlreadyRunning(Exception):
    pass


def submit_job(movie_id, job_type, target, *args, **kwargs):
    """Queue a background job for a movie and return its job id.

    Raises JobAlreadyRunning if the movie already has a queued/running job -
    only one job may be in flight per movie at a time. Different movies run
    concurrently, capped per job_type by the MAX_CONCURRENT_*_JOBS config.
    """
    with _SUBMIT_LOCK:
        conn = db.connect()
        try:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE movie_id = ? AND status IN ('queued', 'running')",
                (movie_id,),
            ).fetchone()
            if existing:
                raise JobAlreadyRunning(
                    f"Movie {movie_id} already has an active job (id={existing['id']})"
                )

            cur = conn.execute(
                "INSERT INTO jobs (movie_id, job_type, status) VALUES (?, ?, 'queued')",
                (movie_id, job_type),
            )
            conn.commit()
            job_id = cur.lastrowid
        finally:
            conn.close()

    thread = threading.Thread(
        target=_run, args=(job_id, movie_id, job_type, target, args, kwargs), daemon=True,
    )
    thread.start()
    return job_id


def _run(job_id, movie_id, job_type, target, args, kwargs):
    semaphore = _SEMAPHORES.get(job_type)
    if semaphore:
        semaphore.acquire()

    conn = db.connect()
    try:
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = datetime('now') WHERE id = ?",
            (job_id,),
        )
        conn.commit()

        def progress(pct, message=None):
            conn.execute(
                "UPDATE jobs SET progress_pct = ?, message = ? WHERE id = ?",
                (pct, message, job_id),
            )
            conn.commit()

        target(movie_id, progress, *args, **kwargs)

        conn.execute(
            "UPDATE jobs SET status = 'done', progress_pct = 100, finished_at = datetime('now') WHERE id = ?",
            (job_id,),
        )
        conn.commit()
    except Exception as e:
        conn.execute(
            "UPDATE jobs SET status = 'error', message = ?, finished_at = datetime('now') WHERE id = ?",
            (str(e), job_id),
        )
        conn.commit()
    finally:
        conn.close()
        if semaphore:
            semaphore.release()


def get_job(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def get_jobs_for_movie(conn, movie_id):
    return conn.execute(
        "SELECT * FROM jobs WHERE movie_id = ? ORDER BY id DESC", (movie_id,)
    ).fetchall()
