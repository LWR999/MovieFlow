import requests

import config

BASE_URL = "https://api.themoviedb.org/3"
TIMEOUT = 10


class TMDbError(Exception):
    pass


def _get(path, params=None):
    if not config.TMDB_API_KEY:
        raise TMDbError("TMDB_API_KEY is not set in .env")
    params = dict(params or {})
    params["api_key"] = config.TMDB_API_KEY
    try:
        resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise TMDbError(f"TMDb request failed: {e}") from e
    if resp.status_code != 200:
        raise TMDbError(f"TMDb returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def search_movies(query, year=None, limit=5):
    params = {"query": query}
    if year:
        params["year"] = year
    data = _get("/search/movie", params)

    candidates = []
    for r in data.get("results", [])[:limit]:
        release_date = r.get("release_date") or ""
        release_year = int(release_date[:4]) if release_date[:4].isdigit() else None
        candidates.append({
            "tmdb_id": r["id"],
            "title": r.get("title"),
            "year": release_year,
            "rating": r.get("vote_average"),
            "poster_path": r.get("poster_path"),
            "original_language": r.get("original_language"),
        })
    return candidates


def get_external_ids(tmdb_id):
    data = _get(f"/movie/{tmdb_id}/external_ids")
    return {"imdb_id": data.get("imdb_id")}
