# MKV Manager — Build Spec for Claude Code

## 0. Context for the implementer

This app runs on `downloadserver.local` as user `dl`, serving on **port 5010**. It manages a
movie download pipeline that goes:

```
/home/dl/torrents/completed/           incoming torrents land here (unsorted), plus TV/ (out of scope)
        │
        ▼  STAGE 0: Incoming (TMDb match + sort into staging/'s category folders)
/home/dl/torrents/staging/{1080p,4K,Blurays,Foreign}
        │
        ▼  STAGE 1: Intake / Pre-processing
        ▼  STAGE 2: Processing (audio track cleanup)
        ▼  STAGE 3: Library (copy to final Plex library paths)
```

`completed/` is scanned by the **Incoming** stage (not Intake) - raw torrent
client output, unsorted. Incoming matches each item against TMDb (same
picker as Intake used to do) and, on confirmation, moves it into
`staging/`'s category folder (`Blurays` for BDMV, `Foreign` if
`original_language != 'en'`, else `4K`/`1080p` by resolution - same rule as
section 1.1). Only once an item has been moved into `staging/` does Intake
see it - by that point it already has a confirmed TMDb match, so Intake no
longer does its own matching; it just lists staged movies ready to
pre-process. `completed/TV/` is out of scope regardless — never touch it.

Existing shell scripts exist for parts of this (MP4→MKV conversion via ffmpeg, possibly BDMV
handling). The user will share these on request if Claude Code gets stuck reverse-engineering
a step from this spec alone — don't guess wildly on encoder flags etc. if a real script exists;
ask for it.

---

## 1. Assumptions to confirm before/while building (flag disagreement if any of these are wrong)

1. **Destination routing after pre-processing:** if the Foreign checkbox is set → `Foreign/`;
   else if resolution is 2160p → `4K/`; else → `1080p/`. `Blurays/` is **intake-only** — nothing
   is ever moved back there. A 4K foreign film goes to `Foreign/`, not `4K/`. **Confirm this.**
2. **Audio retention rule** (from your answers): keep **all** English-language tracks (dubs,
   commentary, director's commentary — anything tagged English) +, for foreign films, the
   original-language track(s) (per TMDb `original_language`). Drop everything else (e.g. that
   Russian-first track on an English film, French/German dubs on an English film). For a foreign
   film, a commentary track *not* in English and *not* the original language (e.g. an English
   film's German dub commentary on a German-original film) — I'll drop it too. Flag if you want
   a different rule here.
3. **No-year / non-Latin filenames:** since matching blocks and requires manual confirmation
   whenever ambiguous, a missing year or a Cyrillic filename just means the auto-search will
   very likely come back empty or low-confidence — which routes to manual title/year entry
   automatically. No special-case code needed beyond "let the user type in title + year and
   re-search."
4. **Folder collisions:** if a proposed destination folder already exists (e.g. reprocessing, or
   a duplicate download), the app **blocks that specific movie** — it doesn't overwrite, doesn't
   auto-suffix, and doesn't skip silently. It shows a clear "destination already exists" state on
   that row and stays blocked until you've manually cleared the collision (e.g. deleted the old
   copy from Plex/the library folder yourself) and re-triggered the move. Other movies in the
   same batch are unaffected — this only blocks the one movie in conflict.
5. Single-user, LAN-only tool — no auth, no HTTPS requirement, no multi-tenancy.

---

## 2. Tech stack

- **Backend:** Flask, single process, `debug=False` in practice but keep `FLASK_DEBUG` env-togglable.
- **DB:** SQLite (file, e.g. `mkv_manager.db`) — tracks every discovered movie, its stage, its
  metadata match, and job/progress state. This is source of truth across restarts (the filesystem
  is scanned to discover new arrivals, but state/decisions persist in SQLite).
- **Background jobs:** plain Python `threading.Thread` per job (per your choice of "simple").
  A `jobs` table holds `id, movie_id, job_type, status (queued/running/done/error), progress_pct,
  message, started_at, finished_at`. Frontend polls `/api/jobs/<id>` (or `/api/jobs?movie_id=`)
  every ~1s while a job is active. No Redis/Celery.
  - Guard against concurrent jobs on the *same* movie (lock at the movie level); different movies
    can run in parallel, but cap concurrency (e.g. max 2 simultaneous ffmpeg/mkvmerge jobs) since
    these are I/O and CPU heavy and this is one box.
- **Metadata:** TMDb API (needs an API key — free, non-commercial tier). Use `/search/movie`
  with `query` + `year`, and pull `external_ids` for the IMDb ID (`ttXXXXXXX`) plus `poster_path`
  for artwork, and `original_language` for the foreign-language default.
- **Media inspection:** `ffprobe` (JSON output, `-show_streams -show_format`) for container/codec/
  resolution/color metadata and audio track languages; `mkvmerge -J` as a second source of truth
  for MKV files specifically (it reports track languages/names cleanly and is what we'll use for
  the actual remux/track-removal operations anyway). For BDMV folders, enumerate `.mpls` files
  and use `mkvmerge -J` or `ffprobe` against each to get durations.
- **Remux/mux tooling:** `mkvmerge`/`mkvtoolnix` (BDMV playlist → MKV, track selection/removal),
  `ffmpeg` (MP4 → MKV container-only remux, `-c copy`).
- **Frontend:** server-rendered Flask templates + a bit of JS (fetch/polling) is enough — no need
  for a SPA framework. Use CSS variables for a dark/light theme toggle (persisted in
  `localStorage` client-side, that's fine since it's UI-only, not app state).

---

## 3. Naming convention

**Plex-safe scheme:** strip characters that are unsafe/awkward across Plex + common filesystems:
`: ' " ? * < > | /` and collapse resulting double-spaces or trailing `-`/space. Colon becomes
` -` (matches common Plex-safe convention, e.g. `Blade Runner 2049` stays clean, `Star Trek:
First Contact` → `Star Trek - First Contact`). Apostrophes just drop (`Ocean's Eleven` →
`Oceans Eleven`).

Folder: `<Clean Title> (<Year>)`
File inside: `<Clean Title> (<Year>).mkv`

No IMDb/TMDb ID tags in the filename per your spec (keep it simple — you didn't ask for
`{tmdb-12345}` tags, so omit them, but **do** store the TMDb/IMDb ID in the SQLite record so
re-matching/debugging later doesn't require re-searching).

---

## 4. Title/year parsing (pre-TMDb-search)

Parse **only** movie name + year from the raw folder/file name — nothing else (no resolution,
codec, group tags need to be extracted, since you're not embedding that data anyway). A pragmatic
approach:
- Strip a trailing 4-digit year (1900–2099) if present — remember it separately.
- Strip everything from the first "quality/source" keyword onward (`1080p`, `2160p`, `720p`,
  `BluRay`, `WEB`, `WEBRip`, `HDTV`, `REMUX`, `x264`, `x265`, `HEVC`, `AAC`, `DTS`, etc.) as a
  safety net, since scene names often put the year *before* these tags.
- Replace `.` and `_` with spaces, collapse whitespace.
- Whatever's left is the candidate title.

This is a best-effort first guess only. **The UI must let you edit title + year and hit
"re-search" before committing** — per your answer, this is a hard requirement, not a nice-to-have.

---

## 5. TMDb matching flow

1. Search `/search/movie?query=<title>&year=<year_if_present>`.
2. If year is missing, search by title alone.
3. **Auto-accept the top result at Incoming discovery time** (revised from the original "never
   auto-accept" design - per your later instruction, matching should run automatically so you
   only have to act on the exceptions). If TMDb returns no candidates, or the search/lookup
   fails, the item just falls back to the manual "needs match" state as before. A "re-match" link
   is always available (on both Incoming and Intake) to open the same TMDb candidate picker
   (poster thumbnail, title, year, TMDb rating, top N e.g. 5) with a manual title/year input to
   re-search, in case the auto-match picked the wrong film.
4. On confirm (auto or manual), store: `tmdb_id`, `imdb_id`, `title` (clean), `year`,
   `original_language`, `poster_path` in the movie's SQLite record. This becomes the proposed
   filename shown on the Intake screen.
5. Foreign-checkbox default = `original_language != 'en'`. User can override the checkbox at any
   time; overriding does **not** re-trigger a re-match, it only affects the audio-track-retention
   rule at the Processing stage.

---

## 6. Stage 0 — Incoming screen, and Stage 1 — Intake screen

### 6a. Stage 0 — Incoming screen

On load, scan `/home/dl/torrents/completed/` (top-level only, not `TV/`) for:
- Top-level `.mkv` or `.mp4` files directly in `completed/`, and
- Subfolders containing `.mkv`/`.mp4` files, or a `BDMV` package.

Anything found that isn't already a recorded row in SQLite (matched by original path) gets a new
row created in `status = incoming`.

Title/year parsing runs immediately, followed by an automatic TMDb match attempt against the top
search result (section 5) - most items arrive already matched, needing no action. Each item also
gets resolution/HDR/audio probing, a type badge, and an editable foreign checkbox, same as Intake
used to show (see section 6b). A "re-match" link is always available (whether auto-matched or
not) to open the TMDb candidate picker and correct a wrong auto-match or match one TMDb couldn't
find automatically; confirming from here returns you to Incoming, not Intake. Once matched,
additionally show the **proposed destination category** (`Blurays` if BDMV, else `Foreign` if the
foreign checkbox is set, else `4K`/`1080p` by resolution - same rule as section 1.1) and a **"Move
to staging"** button. Clicking it moves the raw item (unchanged - no renaming, no remuxing yet)
into `staging/<category>/`, blocking with the same "destination already exists" collision state
as elsewhere if something with that name already exists there. On a successful move, `status`
becomes `discovered` and the row disappears from Incoming, appearing on Intake instead.

`completed/` and `staging/` are on the same filesystem, so this move is a plain rename, not a
copy+verify+delete - no background job/progress needed, it's effectively instant regardless of
file size.

### 6b. Stage 1 — Intake screen

Lists all `status = discovered` movies - i.e. already TMDb-matched and sitting in
`staging/<category>/`, per Stage 0 above. For each item show:
- Proposed IMDb-style filename (clean title + year) and poster thumbnail
- Type badge: `BDMV (unmuxed)` / `MKV` / `MP4 (needs container remux)`
- Resolution + 4K HDR flavor if detected (`HDR10`, `DV`, `HDR10+`, `SDR`) — best-effort, may show
  "unknown" if `ffprobe`/DV detection is inconclusive; that's fine, don't block on it
- Audio track summary (language + codec + channels per track, e.g. `English DTS-HD MA 5.1,
  Russian AC3 5.1, Commentary (English) AC3 2.0`)
- Foreign checkbox (defaulted from TMDb `original_language`, editable) - overriding here still
  does not re-trigger a re-match or move, only affects the audio-track-retention rule
- A "re-match" link back to the same TMDb picker, in case the Stage 0 match was wrong
- Select checkbox (for bulk actions) + per-row delete button

Bulk actions bar: select all / none, "Delete selected", "Pre-process selected".

---

## 7. Stage 1 — Pre-processing (the actual work)

For each selected, confirmed movie, in a background job:

1. **Create destination working folder** in IMDb naming format (working location can be in-place
   first, moved at the end — see step 5).
2. **If MP4:** `ffmpeg -i input.mp4 -c copy -map 0 output.mkv`, verify output file exists and has
   a plausible size/duration (sanity check against source via `ffprobe`), then delete the source
   MP4 only on success.
3. **If BDMV:**
   - Enumerate all `.mpls` files under `BDMV/PLAYLIST/`.
   - For each, get duration (via `mkvmerge -J` or `ffprobe`).
   - Show all playlists with duration + estimated size in the job's review UI; **pre-select the
     longest one**, but require explicit user confirmation before the remux actually runs (per
     your answer — auto-select-but-confirm).
   - On confirm: `mkvmerge <path>/00XXX.mpls -o "<Title> (<Year>).mkv"`.
   - On successful remux (verify output plays / has expected duration), **delete the BDMV source
     folder immediately** (per your answer — no retention).
4. **Rename** the resulting MKV to `<Title> (<Year>).mkv` inside the IMDb-named folder.
5. **Delete any other files** in the folder (NFO, samples, extras, artwork you didn't fetch from
   TMDb, etc.) — anything that isn't the final MKV.
6. **Move the folder** to the correct destination per the routing rule in section 1.1 (confirm
   that assumption).
7. Update SQLite row: `status = preprocessed`, store final path, resolution, etc.

Show per-job progress (ffmpeg/mkvmerge both support progress via stderr parsing or `-progress`
flags — parse this into the `progress_pct` field rather than just spinning).

---

## 8. Stage 2 — Processing (audio track cleanup)

Screen lists all `status = preprocessed` movies, showing resolution badge (1080p/4K) and
foreign badge, with select/deselect all-or-individual.

For each selected movie, background job:
1. `mkvmerge -J` (or `ffprobe`) to enumerate audio tracks with language tags.
2. Determine keep-set per the rule in section 1.2:
   - Keep every track tagged English.
   - If foreign, also keep track(s) tagged as the TMDb `original_language`.
   - Drop everything else.
   - Keep **all subtitle tracks**, untouched (per your answer).
3. Re-mux via `mkvmerge -o output.tmp.mkv --audio-tracks <keep-list> input.mkv` (mkvmerge lets you
   select tracks by ID; build the ID list from step 1's enumeration), then atomically replace the
   original file on success.
4. Update SQLite: `status = processed`.

Progress shown per job as above.

---

## 9. Stage 3 — Library

Screen lists all `status = processed` movies (select/deselect all-or-individual).

For each selected:
1. **Move** (not copy) the whole movie folder to `$ENCODES4K` if resolution is 2160p, else
   `$ENCODES`. If the destination folder already exists, this movie is blocked with the same
   "destination already exists" collision state as section 1.4/7 — resolve manually (e.g. delete
   the old copy from Plex/the library) and re-trigger.
2. Update SQLite: `status = in_library`.

Show progress. A cross-filesystem move (source SSD → NAS-backed `$ENCODES`) is really a copy +
delete-source under the hood — implement it that way explicitly (copy with progress, verify the
copy against the source e.g. size/checksum, then delete the source) rather than relying on
`shutil.move`/`os.rename`, which will either silently fall back to copy+delete anyway or just
fail outright across filesystems. Report progress via byte-count during the copy phase.

---

## 10. Deletion (available at any stage)

Deletes the folder/file(s) from disk and removes (or soft-marks) the SQLite row. No dry-run/
confirmation step per your answer, but the button itself should still require a click + are-you-
sure (not a silent single-click delete) — that's a UI safety minimum, not a "dry run."

---

## 11. UI

**Revised from the original per-stage-screen design.** The five separate screens (Incoming,
Intake, Pre-processing, Processing, Library) were built first and worked, but in practice didn't
"flow" - following one movie's actual journey meant hopping across five disconnected pages, each
showing a different slice of state with no thread connecting them (a job's progress lived in a
separate table you had to correlate by movie name; confirming a match could redirect you to a
screen your movie wasn't even on). The stages/statuses described in sections 6-10 are still
accurate - `incoming` -> `discovered` -> `preprocessed` -> `processed` -> `in_library` - only the
*presentation* changed: one screen instead of five.

- **One "Movies" screen**, not five. Every tracked movie shows in one table regardless of stage.
  Each row computes a single `state` (needs_match / ready_stage / ready_preprocess(_bdmv) /
  ready_process / ready_library / in_progress / error / done) from its status + collision flag +
  most recent job, and shows exactly one contextual action for that state - a link (Match, Choose
  playlist) or a form (Move to staging / Pre-process / Process / Move to library), never more
  than one at a time. An active job replaces the action with a live progress bar directly on that
  row (still polling `/api/jobs/<id>`, reloading the page once the job reaches done/error so the
  row picks up its new state). An errored job shows Retry (re-posts to whichever bulk endpoint
  matches the job's type) and Dismiss (removes just the stale job row) instead.
- **Filter chips** (All / Needs Attention / In Progress / Ready / Done) narrow the same table
  client-side - no navigation, no separate URL.
- **Bulk actions** work per action type: checking rows with the same next action (e.g. several
  "ready to process") enables that action's button in the bulk toolbar with a live count; rows
  needing different actions don't interfere with each other.
- The TMDb match picker and the BDMV playlist picker remain their own focused sub-pages (a picker
  is a genuinely different interaction from a list) - both redirect back to the Movies screen (or
  wherever they were opened from) on confirm, via an explicit `return_to` value rather than
  relying on the browser's `Referer` header.
- Dark/light toggle, persisted client-side, unchanged.
- Poster art: fetched from TMDb and cached locally (section 12 polish), shown as a thumbnail per
  row.

---

## 12. Decisions confirmed

1. **Library stage moves** (not copies) to `$ENCODES`/`$ENCODES4K` — see section 9 for the
   cross-filesystem-safe implementation note (copy+verify+delete-source, not a bare `os.rename`).
2. **Folder collisions block that single movie** until manually resolved (see section 1.4) —
   applies at both the pre-processing destination move (section 7) and the library move
   (section 9).
3. **Posters are UI-display-only** — fetched from TMDb on the fly for the app's own screens, not
   saved into the movie folder. Plex does its own independent metadata/poster fetch later; no
   `folder.jpg`/`poster.jpg` needed.
4. **Concurrency is tunable, not fixed.** Given the pipeline is likely I/O-bound (reading off the
   local SSD in Pre-processing/Processing, writing to a NAS over network in the Library stage),
   different stages probably want different concurrency limits, and the right number depends on
   real-world throughput you haven't measured yet. Build this as a config value per job type
   (e.g. `MAX_CONCURRENT_PREPROCESS_JOBS`, `MAX_CONCURRENT_LIBRARY_JOBS`) defaulting to something
   conservative (2), rather than hardcoding — so you can bump the Library-stage number up if the
   NAS write happens to have spare bandwidth under concurrent streams, or down if concurrent
   copies start thrashing the NAS. Don't try to auto-tune this; just make it a config knob you can
   adjust once you've watched it run.

---

## 13. Suggested build order for Claude Code

1. SQLite schema + Flask skeleton + dark/light shell templates (no logic yet).
2. Filesystem scanner (discovery) + Intake screen rendering real data, no actions yet.
3. TMDb integration + match-confirmation UI.
4. ffprobe/mkvmerge inspection layer (resolution, HDR/DV best-effort, audio track listing) feeding
   the Intake screen's badges.
5. Background job framework (thread + SQLite job table + polling endpoint) — build this once,
   generically, before wiring any specific job type to it.
6. Pre-processing job: MP4→MKV path first (simpler), then BDMV path (playlist detection +
   confirmation UI + remux).
7. Processing stage (audio track stripping).
8. Library stage (copy/move + progress).
9. Deletion actions wired in at every stage.
10. Polish: poster caching, error states, job failure handling/retry.
