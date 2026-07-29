STATE_LABELS = {
    "in_progress": "in progress",
    "error": "error",
    "needs_match": "needs match",
    "ready_stage": "ready to stage",
    "ready_preprocess": "ready to pre-process",
    "ready_preprocess_bdmv": "ready to pre-process",
    "ready_process": "ready to process",
    "ready_library": "ready for library",
    "done": "in library",
}

_MOVIE_WITH_JOB_SELECT = """
    SELECT m.*, j.id AS job_id, j.job_type AS job_type, j.status AS job_status,
           j.progress_pct AS job_progress_pct, j.message AS job_message
    FROM movies m
    LEFT JOIN jobs j ON j.id = (
        SELECT id FROM jobs WHERE movie_id = m.id ORDER BY id DESC LIMIT 1
    )
"""


def describe(row):
    d = dict(row)

    if d["job_status"] in ("queued", "running"):
        d["state"] = "in_progress"
    elif d["job_status"] == "error":
        d["state"] = "error"
    elif not d["tmdb_id"]:
        d["state"] = "needs_match"
    elif d["status"] == "incoming":
        d["state"] = "ready_stage"
    elif d["status"] == "discovered":
        d["state"] = "ready_preprocess_bdmv" if d["source_type"] == "bdmv" else "ready_preprocess"
    elif d["status"] == "preprocessed":
        d["state"] = "ready_process"
    elif d["status"] == "processed":
        d["state"] = "ready_library"
    else:
        d["state"] = "done"

    d["state_label"] = STATE_LABELS[d["state"]]
    return d


def intake_movies(conn):
    rows = conn.execute(_MOVIE_WITH_JOB_SELECT + " WHERE m.status = 'incoming' ORDER BY m.created_at DESC").fetchall()
    return [describe(r) for r in rows]


def workbench_movies(conn):
    rows = conn.execute(
        _MOVIE_WITH_JOB_SELECT + " WHERE m.status IN ('discovered', 'preprocessed') ORDER BY m.updated_at DESC"
    ).fetchall()
    return [describe(r) for r in rows]


def library_queue_movies(conn):
    rows = conn.execute(
        _MOVIE_WITH_JOB_SELECT + " WHERE m.status = 'processed' ORDER BY m.updated_at DESC"
    ).fetchall()
    return [describe(r) for r in rows]


def movie_with_job(conn, movie_id):
    row = conn.execute(_MOVIE_WITH_JOB_SELECT + " WHERE m.id = ?", (movie_id,)).fetchone()
    return describe(row) if row else None
