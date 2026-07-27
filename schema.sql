DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS movies;

CREATE TABLE movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL, -- 'bdmv', 'mkv', 'mp4'
    status TEXT NOT NULL DEFAULT 'discovered', -- discovered, preprocessed, processed, in_library
    title TEXT,
    year INTEGER,
    clean_title TEXT, -- Plex-safe title used for naming
    tmdb_id INTEGER,
    imdb_id TEXT,
    original_language TEXT,
    poster_path TEXT,
    is_foreign INTEGER NOT NULL DEFAULT 0,
    resolution TEXT, -- e.g. '1080p', '2160p'
    hdr_flavor TEXT, -- HDR10, DV, HDR10+, SDR, unknown
    final_path TEXT,
    collision INTEGER NOT NULL DEFAULT 0, -- destination-already-exists block (section 1.4/7/9)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movie_id INTEGER NOT NULL REFERENCES movies(id),
    job_type TEXT NOT NULL, -- preprocess, process, library_move
    status TEXT NOT NULL DEFAULT 'queued', -- queued, running, done, error
    progress_pct REAL NOT NULL DEFAULT 0,
    message TEXT,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX idx_jobs_movie_id ON jobs(movie_id);
