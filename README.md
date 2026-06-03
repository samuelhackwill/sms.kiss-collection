# IA Kissing Pipeline

A local-first curation tool for finding, reviewing, clipping, and tagging scenes from public Internet Archive films.

The project started as a "find kissing scenes" pipeline, but the current web app supports multiple scene tags such as `kiss`, `phone`, `cry`, and `dance`. It is built around manual skimming: the pipeline downloads a film, builds a fast skim-preview video, and lets a reviewer mark moments, extract rough clips, tag them, and store precise event timing for downstream apps.

## Stack

- Python 3.13+
- Flask web app
- SQLite database
- FFmpeg / ffprobe for video download inspection, frame extraction, skim previews, clips, and crops
- Pillow for frame numbering overlays
- Internet Archive advanced search and metadata APIs
- `uv` for local development and command execution
- Optional Nginx + systemd deployment files under `deploy/`

## Main Features

- Ingest film metadata from Internet Archive.
- Metadata-screen films before video work.
- Download source films and build skim previews.
- Review films in a browser using mouse/finger scrubbing.
- Add marks, assign one tag per mark, and build clips from marks.
- Store clip metadata, including precise `kiss_start_seconds` and `kiss_end_seconds` when applicable.
- Serve a clip API for downstream apps.
- Hide ignored clips from the API and media endpoint.
- Explore local video artifacts through `/review_data`.
- Requeue a movie when a source file exists but skim generation or DB state got stale.

## Repository Layout

```text
src/ia_kissing_pipeline/
  config.py              Runtime configuration from environment variables
  db.py                  SQLite schema and connection helpers
  main.py                CLI pipeline commands
  webapp.py              Flask review app and API
  ingest/                Internet Archive ingest and storage
  scoring/               Metadata/text/right scoring helpers
  video/                 FFmpeg-backed video operations

scripts/
  getmorevids.py         SSH-friendly batch downloader / skim builder

deploy/
  nginx/                 Example Nginx virtual host
  systemd/               Example systemd unit
  SETUP_NGINX.md         Deployment notes

tests/
  pytest test suite
```

Runtime data lives under `data/` by default and is intentionally ignored by Git.

## Requirements

Install system tools:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

Install Python dependencies with `uv`:

```bash
uv sync --extra dev
```

If you do not use `uv`, create a Python 3.13+ virtualenv and install the package in editable mode.

## Configuration

Configuration is environment-variable based. Defaults are relative to the current working directory.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development` | App environment label |
| `DB_PATH` | `./data/pipeline.db` | SQLite database path |
| `CACHE_DIR` | `./data/cache` | Internet Archive metadata/search cache |
| `DOWNLOAD_DIR` | `./data/downloads` | Downloaded source films |
| `FRAME_DIR` | `./data/frames` | Extracted/keyframe artifacts |
| `PREVIEW_DIR` | `./data/previews` | Skim preview videos |
| `CLIPS_DIR` | `./data/clips` | Saved review clips |
| `LOG_DIR` | `./data/logs` | Runtime logs |
| `USER_AGENT` | `ia-kissing-pipeline/0.1 (...)` | Internet Archive request user agent |
| `IA_KISSING_SKIM_TIMEOUT_SECONDS` | `180` | Per-film skim build timeout in `scripts/getmorevids.py` |
| `IA_KISSING_DISABLE_QUEUE_FILL` | unset | Test/dev switch to disable legacy queue fill behavior |
| `IA_KISSING_USE_CODEX_TEXT_GATE` | `1` | Enables optional Codex text gate when that path is used |
| `IA_KISSING_CODEX_MODEL` | `gpt-5.1-codex-mini` | Optional Codex model for text gating |
| `IA_KISSING_CODEX_TIMEOUT_SECONDS` | `45` | Optional Codex text gate timeout |
| `IA_KISSING_CODEX_WORKDIR` | `/tmp/codex-text-gate` | Optional Codex text gate workdir |
| `IA_KISSING_WEB_HOST` | `127.0.0.1` | Flask bind host |
| `IA_KISSING_WEB_PORT` | `8000` | Flask bind port |

Start from the example file:

```bash
cp .env.example .env
```

The app does not automatically load `.env`; export values in your shell, service file, or process manager.

## Database

Initialize the database:

```bash
uv run python -m ia_kissing_pipeline.main init-db
```

The schema is created in `src/ia_kissing_pipeline/db.py`. Important tables include:

- `films`: Internet Archive film metadata and pipeline status.
- `film_files`: source files listed by Internet Archive.
- `analysis_jobs`: durable job records for skim builds, clips, and batch runs.
- `manual_marks`: reviewer marks on skim previews.
- `manual_clips`: extracted clips, tag, ignore state, crop data, and metadata JSON.
- `film_reviews`: per-film review status.
- `app_settings`: small runtime settings such as clip API ordering mode.

## Web App

Run locally:

```bash
uv run ia-kissing-web
```

Default URL:

```text
http://127.0.0.1:8000
```

Useful routes:

- `/`: next reviewable film.
- `/films`: film database, tag filters, clip drawers, clip API mode toggle.
- `/films/<id>`: film review page.
- `/clips`: bare clip gallery.
- `/review_data`: local video artifact explorer.
- `/api/random-clips`: clip API for another frontend.
- `/media/<kind>/<path>`: media file serving for downloads, previews, and clips.

## Downloading More Films

The web app does not automatically download more films on page load. Use the SSH-friendly script explicitly:

```bash
uv run python scripts/getmorevids.py 5
```

From another machine:

```bash
ssh bot@sms-clips.samuel.ovh 'cd /home/bot/ia-kissing-pipeline && .venv/bin/python scripts/getmorevids.py 5'
```

What the script does:

1. Expands the active candidate pool by ingesting Internet Archive films.
2. Runs metadata scoring.
3. Sequentially downloads/builds skim previews for ready candidates.
4. Stops when there are no more ready candidates for that explicit run.

The current ingest query is defined in `webapp.py`:

```python
QUEUE_INGEST_QUERY = "collection:feature_films"
```

## Review Workflow

1. Run `scripts/getmorevids.py` to build skim previews.
2. Open `/films` or `/`.
3. Open a `pending` film.
4. Scrub the skim preview horizontally.
5. Click/tap to create a mark.
6. Assign a tag to the mark.
7. Build a rough clip.
8. Optionally set precise kiss timing inside the clip.
9. Mark the film reviewed or reviewed-no-kiss.

Clips preserve structured metadata in `manual_clips.metadata_json`. For kiss clips, this can include:

```json
{
  "kiss_start_seconds": 20.0,
  "kiss_end_seconds": 22.5
}
```

Those values are clip-relative.

## Clip API

Fetch clips:

```text
GET /api/random-clips?limit=12
GET /api/random-clips?tag=kiss&limit=12
```

The API returns active, non-ignored clips. It includes `kiss_start_seconds` and `kiss_end_seconds` when present so downstream apps can soft-trim playback.

The `/films` page has a Clip API mode toggle:

- `Random`: random clips.
- `Ordered`: stateful ordered mode using `manual_clips.id`, with separate cursors per tag.

## CORS

CORS is added for `/api/` and `/media/` paths for development frontends:

- `http://localhost:3000`
- `http://127.0.0.1:3000`
- `http://10.73.73.*:3000`

Adjust this in `webapp.py` if your frontend origin changes.

## Review Data Page

`/review_data` scans local video files and links them back to DB rows when possible.

Sections:

- `Downloaded Sources`: source movie files under `DOWNLOAD_DIR`.
- `Skim Previews`: skim videos under `PREVIEW_DIR`.
- `Saved Clips`: extracted clips under `CLIPS_DIR`.
- `Loose Data Videos`: video files directly under the data root.

DB-linked files show `Requeue movie`. True stray files can be deleted from the page.

## Deployment

Example deployment files are included:

- `deploy/systemd/ia-kissing-web.service`
- `deploy/nginx/sms-clips.samuel.ovh.conf`
- `deploy/SETUP_NGINX.md`
- `deploy/deploy.sh`
- `.github/workflows/deploy.yml`

For a detached ad hoc run:

```bash
setsid -f bash -lc 'cd /home/bot/ia-kissing-pipeline && uv run python -m ia_kissing_pipeline.webapp > data/logs/webapp-standalone.log 2>&1 < /dev/null'
```

For a production-ish VPS deployment, prefer systemd:

```bash
sudo cp deploy/systemd/ia-kissing-web.service /etc/systemd/system/ia-kissing-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now ia-kissing-web.service
```

The repository also includes a GitHub Actions workflow for push-to-deploy. It runs the web tests, uploads the checked-out code to the VPS with `rsync`, then SSHes into the VPS and executes:

```bash
/home/bot/ia-kissing-pipeline/deploy/deploy.sh
```

Required GitHub Actions secrets:

- `DEPLOY_HOST`: VPS hostname or IP, for example `sms-clips.samuel.ovh`.
- `DEPLOY_USER`: SSH user, for example `bot`.
- `DEPLOY_SSH_KEY`: private SSH key allowed to log in as that user.

The VPS must already have:

- an app directory at `/home/bot/ia-kissing-pipeline`
- `uv`
- FFmpeg
- the systemd unit installed
- permission for the deploy user to restart `ia-kissing-web.service`

If using sudo for restarts, add a narrow sudoers rule with `visudo`:

```text
bot ALL=NOPASSWD: /usr/bin/systemctl restart ia-kissing-web.service
```

Or install the provided snippet:

```bash
sudo cp deploy/sudoers/ia-kissing-web /etc/sudoers.d/ia-kissing-web
sudo chmod 0440 /etc/sudoers.d/ia-kissing-web
sudo visudo -cf /etc/sudoers.d/ia-kissing-web
```

The workflow excludes `.git/`, `.venv/`, `.env*`, and `data/` from upload. Keep runtime files in `data/`, not in Git.

## Tests

Run the web tests:

```bash
uv run pytest tests/test_webapp.py
```

Run everything:

```bash
uv run pytest
```

## Git Hygiene

Runtime files are ignored:

- SQLite DBs and WAL/SHM files.
- Downloaded source movies.
- Generated skim previews.
- Generated clips.
- Extracted frames.
- Runtime logs and caches.
- Local virtualenvs and Python caches.

Do not publish the contents of `data/`; it may contain large media files, generated clips, and local review state.
