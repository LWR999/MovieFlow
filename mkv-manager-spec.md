# MKV Manager — Build Spec for Claude Code

## 0. Context for the implementer

This app runs on `downloadserver.local` as user `dl`, serving on **port 5010**. It manages a
movie download pipeline that goes:

```
/home/dl/torrents/completed/           incoming torrents land here (unsorted), plus TV/ (out of scope)
        │
        ▼  status: incoming     (TMDb match + sort into staging/'s category folders)
/home/dl/torrents/staging/{1080p,4K,Blurays,Foreign}
        │
        ▼  status: discovered   (pre-processing: container remux / BDMV playlist remux)
        ▼  status: preprocessed (audio track cleanup)
        ▼  status: processed    (copy to final Plex library paths)
        ▼  status: in_library
```

`completed/` is scanned for raw, unsorted torrent client output (never `TV/`
— out of scope regardless). Each item is matched against TMDb (top result
auto-accepted, re-match always available - section 5) and, via a "Move to
staging" action, moved into `staging/`'s category folder (`Blurays` for
BDMV, `Foreign` if `original_language != 'en'`, else `4K`/`1080p` by
resolution - same rule as section 1.1).

In practice only 2-3 movies are ever being worked on at once, one at a time
- the UI (section 11) is built around that reality rather than a bulk/batch
model: pick one movie from Intake, push it through pre-processing and
processing on its own page, then commit everything that's fully prepped to
the library together, in one step.

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
   auto-suffix, and doesn't skip silently. It shows a clear "destination already exists" state,
   with the exact conflicting path (persisted on the movie row, not just in the transient job
   message, so it's still visible after the error job is dismissed), and stays blocked until
   you've manually cleared the collision (e.g. deleted the old copy from Plex/the library folder
   yourself) and re-triggered the move. Other movies are unaffected — this only blocks the one
   movie in conflict.
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
3. **Auto-accept the top result at Intake discovery time** (revised from the original "never
   auto-accept" design - per your later instruction, matching should run automatically so you
   only have to act on the exceptions). If TMDb returns no candidates, or the search/lookup
   fails, the item just falls back to the manual "needs match" state as before. A "re-match" link
   is always available on the movie detail page (section 11), regardless of stage, to open the
   same TMDb candidate picker (poster thumbnail, title, year, TMDb rating, top N e.g. 5) with a
   manual title/year input to re-search, in case the auto-match picked the wrong film.
4. On confirm (auto or manual), store: `tmdb_id`, `imdb_id`, `title` (clean), `year`,
   `original_language`, `poster_path` in the movie's SQLite record. This becomes the proposed
   filename shown on the Intake screen.
5. Foreign-checkbox default = `original_language != 'en'`. User can override the checkbox at any
   time; overriding does **not** re-trigger a re-match, it only affects the audio-track-retention
   rule at the Processing stage.

---

## 6. Stage 0 — Intake, and Stage 1 — Workbench (pre-processing readiness)

### 6a. Stage 0 — Intake

On load, scan `/home/dl/torrents/completed/` (top-level only, not `TV/`) for:
- Top-level `.mkv` or `.mp4` files directly in `completed/`, and
- Subfolders containing `.mkv`/`.mp4` files, or a `BDMV` package.

Anything found that isn't already a recorded row in SQLite (matched by original path) gets a new
row created in `status = incoming`.

Title/year parsing runs immediately, followed by an automatic TMDb match attempt against the top
search result (section 5) - most items arrive already matched, needing no action. The Intake
screen itself (section 11) is a thin list - poster thumbnail, title/year or raw name, match
status, delete button; clicking a row opens the movie detail page, where resolution/HDR/audio
probing, the editable foreign checkbox, the "re-match" link, and the **"Move to staging"** action
all live. Clicking "Move to staging" moves the raw item (unchanged - no renaming, no remuxing
yet) into `staging/<category>/` (`Blurays` if BDMV, else `Foreign` if the foreign checkbox is
set, else `4K`/`1080p` by resolution - section 1.1), blocking with the same "destination already
exists" collision state as elsewhere if something with that name already exists there. On a
successful move, `status` becomes `discovered` and the movie moves from Intake to Workbench.

`completed/` and `staging/` are on the same filesystem, so this move is a plain rename, not a
copy+verify+delete - no background job/progress needed, it's effectively instant regardless of
file size.

### 6b. Stage 1 — Workbench

Lists all `status IN (discovered, preprocessed)` movies - i.e. already TMDb-matched, sitting in
`staging/<category>/`, and somewhere between "ready to pre-process" and "ready to process audio."
Like Intake, the Workbench screen itself (section 11) is a thin list with a live progress bar for
whichever movie has an active job; all of the detail - proposed filename, type badge
(`BDMV (unmuxed)` / `MKV` / `MP4 (needs container remux)`), resolution/HDR flavor, audio track
summary, foreign checkbox, re-match link, and the single next action (Pre-process / Choose BDMV
playlist / Process audio) - lives on the movie detail page (section 11), opened by clicking the
row.

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

These movies appear in the Workbench (section 6b/11) alongside pre-processing-ready ones; the
resolution/foreign badges live on the movie detail page, along with the single "Process audio"
action that triggers the job below.

For the selected movie, background job:
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

The Ready for Library queue (section 11) lists all `status = processed` movies - i.e. fully
prepped, waiting to be committed. Rather than per-movie selection, there's a single "Commit all
to library" action that runs the steps below for every movie in the queue at once (each still an
independent job/collision-block - one movie's collision doesn't hold up the others):

1. **Move** (not copy) the whole movie folder to `$ENCODES4K` if resolution is 2160p, else
   `$ENCODES`. If the destination folder already exists, this movie is blocked with the same
   "destination already exists" collision state as section 1.4/7 — resolve manually (e.g. delete
   the old copy from Plex/the library) and re-trigger (re-running "Commit all" picks up only the
   movies still stuck at `processed`).
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

**Revised twice now.** The original five separate screens (Incoming, Intake, Pre-processing,
Processing, Library) worked but didn't "flow" - hopping across five disconnected pages per movie,
with a job's progress living in a separate table you had to correlate by name. That was collapsed
into a single "Movies" dashboard table with bulk selection and filter chips. In practice, though,
usage is never bulk - only 2-3 movies are ever in flight at once, worked one at a time - so the
bulk-selection model was solving a problem that didn't exist, while burying real problems: a
collision's conflicting path vanished the moment you dismissed its error job, the Foreign
checkbox silently did two different things depending on when you toggled it, and BDMV movies
couldn't even be bulk-selected in the first place. The stages/statuses in sections 6-10 are still
accurate - `incoming` -> `discovered` -> `preprocessed` -> `processed` -> `in_library` - only the
*presentation* changed again, this time to match how the tool is actually used: three thin queues
plus one rich page where all real work happens.

- **Intake** (`status = incoming`) - a minimal list: poster, title/year (or the raw parsed name
  if unmatched yet), a status badge, delete. Click a row to open its detail page.
- **Workbench** (`status IN (discovered, preprocessed)`) - the 2-3-movies-in-flight set. Same
  minimal list, plus a live progress bar (polling `/api/jobs/<id>`, page reload on done/error)
  for whichever row has an active job. No hard cap on how many can be in the Workbench at once -
  it's just wherever the natural workflow leaves things.
- **Ready for Library** (`status = processed`) - everything fully prepped. One "Commit all to
  library" button (with an are-you-sure confirmation, since this step deletes the staging source
  once the copy verifies) rather than per-movie selection - see section 9.
- **Movie detail page** (one per movie, `/movies/<id>`) - where the actual work happens,
  regardless of stage:
  - Full info always visible at once - match status, type/resolution/HDR/audio, the Foreign
    checkbox (labeled with what it currently affects: staging category before the move, only
    audio-track retention after), and the *previous* job's error message if any - rather than
    collapsing everything to a single badge.
  - A persistent collision banner shows the exact conflicting destination path (stored on the
    movie row - section 1.4 - so it survives dismissing the error job), not just a generic
    warning icon.
  - One next-action button per stage (Match / Move to staging / Pre-process / Process audio),
    which doubles as Retry - resubmitting is safe even without dismissing the old error first,
    since a job in `error` status doesn't block a new one.
  - Retry (same button, re-post) + Dismiss (clears the stale job row without resubmitting,
    falling back to the plain ready-to-act state) on an errored job.
  - Delete, same are-you-sure requirement as everywhere else.
- The TMDb match picker and the BDMV playlist picker remain their own focused sub-pages (a picker
  is a genuinely different interaction from a list) - both redirect back to the movie detail page
  (or wherever they were opened from) on confirm, via an explicit `return_to` value rather than
  relying on the browser's `Referer` header.
- Dark/light toggle, persisted client-side, unchanged.
- Poster art: fetched from TMDb and cached locally (section 12 polish) - a thumbnail on the list
  views, larger on the detail page.

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
