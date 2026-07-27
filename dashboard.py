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

BULK_GROUP_ENDPOINTS = {
    "stage": "bulk_move_to_staging",
    "preprocess": "bulk_preprocess",
    "process": "process_selected",
    "library": "library_selected",
}

JOB_TYPE_RETRY_ENDPOINT = {
    "preprocess": "bulk_preprocess",
    "process": "process_selected",
    "library_move": "library_selected",
}


def _describe(row):
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

    d["bulk_group"] = {
        "ready_stage": "stage",
        "ready_preprocess": "preprocess",
        "ready_process": "process",
        "ready_library": "library",
    }.get(d["state"])

    if d["collision"] or d["state"] in ("needs_match", "error"):
        d["filter_group"] = "needs_attention"
    elif d["state"] == "in_progress":
        d["filter_group"] = "in_progress"
    elif d["state"] == "done":
        d["filter_group"] = "done"
    else:
        d["filter_group"] = "ready"

    d["retry_endpoint"] = JOB_TYPE_RETRY_ENDPOINT.get(d["job_type"])

    return d


def annotate_movies(conn):
    rows = conn.execute(
        """
        SELECT m.*, j.id AS job_id, j.job_type AS job_type, j.status AS job_status,
               j.progress_pct AS job_progress_pct, j.message AS job_message
        FROM movies m
        LEFT JOIN jobs j ON j.id = (
            SELECT id FROM jobs WHERE movie_id = m.id ORDER BY id DESC LIMIT 1
        )
        ORDER BY m.created_at DESC
        """
    ).fetchall()
    return [_describe(row) for row in rows]
