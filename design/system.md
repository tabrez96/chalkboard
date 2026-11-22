# Chalkboard — System design

## Overview
Chalkboard helps a teacher build a course from sources they trust. You add the files and web pages you rely on, and Chalkboard turns them into a course overview that follows your curriculum board's syllabus, then into chapters, then into lesson plans that fit your class length and timetable. Every sentence it writes shows which source it came from, and a check step flags any sentence its source doesn't back up. On the day, you teach from the lesson plan with an assistant that answers from your sources (or from the web when you ask) and always says which, plus a short trivia quiz for the end of class. Everything runs on your own machines: the AI models run on your own Ollama server, and nothing is sent to an outside AI service.

## Stack
All versions checked on 2026-11-22 against PyPI, Docker Hub, python.org and the projects' release pages. Pin exact versions in `pyproject.toml` / `uv.lock` and the Compose file.

| Part | Choice | Version | Reason |
|---|---|---|---|
| Language | Python | 3.15.2 | Current stable; best PDF/text and HTTP tooling for this job. |
| Web framework | Flask | 3.1.3 | Every screen is a server-rendered page; Flask's templates, sessions and built-in CLI (`flask reset-password`, as the S01 spec expects) fit that directly. Long work runs in the worker, so async adds nothing. |
| Forms / CSRF | Flask-WTF | 1.3.0 | CSRF protection and form validation for every POST. |
| ORM | SQLAlchemy (plain, 2.x style, sync) | 2.1.9 | Chosen ORM; plain SQLAlchemy with a request-scoped session keeps models usable from the web app, worker and CLI alike. |
| Migrations | Alembic | 1.20.1 | Standard SQLAlchemy migrations. |
| Database | PostgreSQL | 19.0 | Current stable; one database for data, the job queue and vectors. |
| Vector search | pgvector extension (image `pgvector/pgvector:0.8.7-pg19`) + `pgvector` Python | 0.8.7 / 0.5.1 | Vectors live next to the chunks they belong to; HNSW index for cosine search. |
| DB driver | psycopg | 3.3.9 | Current PostgreSQL driver for SQLAlchemy. |
| Page interactivity | HTMX (vendored in `static/`) | 4.0.1 | Partial updates (job progress, chat, passage panel) without a JS build or SPA. No CDN. |
| Templates | Jinja2 | 3.1.6 | Ships with Flask. |
| HTTP client | httpx | 0.28.1 | Ollama, SearXNG and page fetches through one guarded client; `MockTransport` for tests. |
| PDF text | pypdf | 7.1.1 | Pure Python, permissive licence; no OCR (scanned PDFs fall back to "No units found" / "Couldn't read the file"). |
| Word text | python-docx | 1.2.0 | .docx extraction. |
| Web page text | trafilatura | 2.3.1 | Main-text extraction from saved pages. |
| Passwords | argon2-cffi | 25.1.0 | Argon2id hashing. |
| Config | pydantic-settings | 2.16.3 | Typed settings from environment (`CHALKBOARD_*`). |
| App server | gunicorn | 27.0.1 | Standard WSGI server in the container. |
| Model server | Ollama (separate Proxmox host, current release v0.40.17) | — | Owner's server; called over its HTTP API only. Base URL is configuration. |
| Embedding model | `qwen3-embedding:0.6b` (default), output fixed to 1024 dimensions | — | Qwen embedding as decided; 0.6b runs acceptably on CPU. `:4b`/`:8b` can be configured and still produce 1024-dim vectors via the `dimensions` option, so the schema never changes (re-embedding is still required). |
| Writing / check / chat model | `qwen3.5:9b` (default) | — | Fits a 16 GB RAM or 8 GB GPU host; `qwen3.6:27b` if the server allows. Final choice after a trial on real material. |
| Web search | SearXNG container (`searxng/searxng`, pin the current dated tag, 2026.11.21 at design time) | — | Self-hosted metasearch with a JSON API; only used when the teacher picks "Sources + web" and it is enabled in Settings. |
| Packaging | uv | 0.13.9 | Lockfile and fast installs. |
| Tests | pytest | 9.2.0 | Unit and integration tests against a real PostgreSQL. |
| Formatter (Python) | ruff (`ruff format` + `ruff check`) | latest 0.17.x, pinned | Handbook default. Check: `ruff format --check . && ruff check .` |
| Formatter (CSS, JS, Markdown, YAML, JSON) | Prettier | 3.10.1 | Handbook default for web files. `templates/` (Jinja) is in `.prettierignore` because Prettier corrupts Jinja tags. Check: `npx prettier --check .` |
| Hosting | Docker Compose on the app host (services: `web`, `worker`, `db`, `searxng`) | — | One command to run; Ollama stays on its own host, reachable only from the app host on the private network. |
| CI | GitHub Actions | — | Formatter checks + pytest with a PostgreSQL service container. |

## Components
- **web** (Flask app, `chalkboard/`): blueprints `auth` (S01), `subjects` (S02), `sources` (S03), `overview` (S04), `chapters` (S05), `today` (S06, assistant, trivia), `students` (S07), `settings` (S08), `guide` (S09, public). Server-rendered Jinja templates matching the screens; HTMX for job progress polling, chat, passage panel and row actions. Design tokens from `ux.md` as CSS variables for light and dark; Raleway and Inter woff2 bundled in `static/fonts/`.
- **worker** (same image, `flask worker`): pulls jobs from the `jobs` table with `SELECT … FOR UPDATE SKIP LOCKED`; job kinds: `ingest_source`, `save_page`, `detect_units`, `generate_overview`, `generate_chapter`, `split_chapter`, `write_trivia`, `replace_trivia_question`, `reembed_all`, `backup`. Jobs that need Ollama and find it down are parked as "waiting for Ollama" and retried with backoff. Also runs the daily backup.
- **ai package** (`chalkboard/ai/`), the one interface to models:
  - `LLMClient` protocol (`generate(prompt, schema) -> dict`, `chat(messages) -> str`) and `Embedder` protocol (`embed(texts) -> list[vector]`); `OllamaClient` implements both over `/api/chat` (with `format` = JSON schema for structured output) and `/api/embed`. A different model or endpoint is configuration only.
  - `RedactingClient` wraps every client: before any request it replaces the subject's student names (and the account's) with neutral placeholders (`Student A`). It is the only way the rest of the code can reach a model.
  - `health()` → reachable / model missing (with the `ollama pull` command) / ok; used by S08 Test connection, the sidebar status dot and the "Can't reach the Ollama server" banner.
- **outbound guard** (`chalkboard/net.py`): the single httpx transport for the whole app. It allows only (a) the configured Ollama host, (b) the configured SearXNG host, (c) page fetches whose host is on the subject's ticked sites (or any host in open mode). Redirects are followed manually and every hop is re-checked; a disallowed host raises before any connection is opened. Hosts that resolve to private addresses are refused for page fetches.
- **sources** (`chalkboard/sources/`): extractors (pypdf, python-docx, plain text/Markdown, trafilatura for HTML), a chunker (~500-token passages with overlap, keeping page numbers), embedding into `chunks.embedding`. Files stored on a Docker volume under `data/uploads/<subject>/<source>`.
- **syllabus units** (`chalkboard/sources/units.py`): heuristic first (lines matching `Unit`/`Chapter` + arabic or roman numeral + title, as used by CBSE/ICSE/state syllabi), LLM extraction with a JSON schema second; if both find nothing, "No units found" and the teacher types the units. The teacher can always correct the list.
- **retrieval** (`chalkboard/ai/retrieve.py`): embed the query, cosine top-k over the subject's chunks (syllabus excluded from teaching citations), returning passages labelled `P1…Pn`.
- **writer** (`chalkboard/ai/write.py`): builds prompts for overview, chapter, lesson plan, trivia and chat; the model returns JSON: sentences, each with its passage labels. Labels map back to chunk ids; a sentence with no valid citation is retried once, then dropped (never shown uncited).
- **checker** (`chalkboard/ai/check.py`): for each (sentence, cited chunk) pair, one structured call: `{"supported": bool, "reason": str}`. Any unsupported pair marks the sentence "Check this" with the reason. Runs as the last step of every generation job (the third step on the generating state).
- **timetable** (`chalkboard/timetable.py`, pure): class days + start times + term dates → ordered class slots; budget = slots × class length; placement of approved lessons onto slots in order (Next class, Coming up, Today, "won't fit", "term is over").
- **web search** (`chalkboard/ai/websearch.py`): SearXNG JSON API; top results (title, URL, snippet) go to the chat prompt; the answer is labelled "From the web" with the links.
- **backups** (`chalkboard/backup.py`): `pg_dump -Fc` plus the uploads folder into one `.tar.gz` in the `backups` volume; keeps the last 14; restore is a documented CLI command (`flask restore <file>`).
- **CLI**: `flask create-teacher`, `flask reset-password`, `flask worker`, `flask backup`, `flask restore`.

## Data model
All tables carry `id`, `created_at`. Every row below `teachers` belongs to one teacher; queries are always scoped to the signed-in teacher.
- `teachers`: email (unique), password_hash, name, theme (`light|dark|system`), sidebar_collapsed, ollama_url, embed_model, write_model, web_search_enabled, open_mode, last_subject_id.
- `allowed_sites`: teacher_id, domain (normalised host, subdomains included).
- `subjects`: teacher_id, name, grade, class_minutes, colour, board (`cbse|icse|state|ib|none`), state_board_name, source_mode (`uploads|websites`), term_start, term_end (nullable).
- `subject_sites`: subject_id, allowed_site_id (the ticked sites).
- `class_times`: subject_id, weekday (0–5), start_time.
- `students`: subject_id, name, note. Names never leave the app (see RedactingClient).
- `sources`: subject_id, number (stable per subject, null while it is the syllabus), kind (`upload|page`), title, origin (domain or "Uploaded"), file_path, url, status (`reading|waiting_ollama|ready|blocked|failed`), progress, is_syllabus (partial unique index: one per subject), embed_model.
- `chunks`: source_id, ordinal, text, page_from, page_to, embedding `vector(1024)` (HNSW, cosine).
- `syllabus_units`: source_id, number, title, text, typed_by_teacher.
- `plan_versions` (the versioned drafts; content is insert-only): subject_id, kind (`overview|chapter`), chapter_key (null for overview), number (1, 2, …), parent_version_id, change_request (text that produced it), status_job_id, created_at. Content rows are never updated; a DB trigger rejects UPDATE of content columns.
- `plan_items`: version_id, ordinal, kind (`chapter|section|lesson`), title, hours_estimate, unit_number, notes.
- `sentences`: version_id, item_id, ordinal, text, check_flag (bool), check_reason.
- `citations`: sentence_id, chunk_id (nullable after source deletion → shown as "source deleted"), source_number.
- `approvals`: version_id, approved_at (approval is a separate row, so versions stay immutable).
- `unit_dispositions`: subject_id, unit_number, state (`not_this_term`).
- `lessons`: chapter_version_id, ordinal, title, minutes, steps (JSON: time, what to say/do, sentence ids). Created by "Approve and split" (hours ÷ class length, rounded up).
- `trivia_sets`: lesson_id, number (versioned like plans), questions (JSON: question, answer, citation chunk id, edited flag).
- `chat_messages`: lesson_id, role, text, scope (`sources|web`), label (`sources|not_in_sources|web`), citations / links (JSON).
- `jobs`: kind, payload (JSON), state (`queued|running|waiting_ollama|done|failed`), step, progress, error, attempts, run_after.
- `backups`: file_path, size, created_at.

## Key flows
1. **Add a source** (S03): upload → file saved, `sources` row "Reading", `ingest_source` job queued → worker extracts text, chunks, embeds via Ollama → "Ready · N passages". Ollama down: text kept, status "Saved · waiting for Ollama", retried later. Save page: the link is checked against the subject's ticked sites (or open mode) before any request; disallowed → "This site isn't chosen for …" and nothing is fetched. Marking a source as the syllabus (S02 form or Use as syllabus) runs `detect_units`.
2. **Generate and approve the overview** (S04): Generate → `generate_overview` job: (a) find passages: for each syllabus unit in order (or, without a syllabus, from a source summary pass) retrieve top passages; (b) write: the model returns chapters (title, unit number, hours fitted to the timetable budget) and cited sentences; uncited sentences retried/dropped; (c) check every sentence against each cited chunk → "Check this" flags. Result is a new `plan_versions` row. Coverage = syllabus units minus units cited by chapters minus "Not this term". Request changes → same job with the previous version and the request text → version N+1. Approve → `approvals` row.
3. **Chapter to lessons to Today** (S05, S06): next chapter's topic comes from the approved overview → `generate_chapter` (same find/write/check) → review → Approve and split → `split_chapter` writes N = ceil(hours × 60 / class_minutes) lesson plans with timed steps and citations → timetable places lessons on class slots → S06 shows today's lesson. Assistant: question → names redacted → retrieve passages → answer with citations, or "Not in your sources" when the model reports no support (checked by the checker); with "Sources + web" and web search enabled, SearXNG results are added and web-based answers are labelled "From the web" with links. After class, `write_trivia` produces 3–5 cited questions.

## Testing approach
- Commands (also in README "Development" and CI):
  - `docker compose up -d db` then `uv run pytest`
  - `uv run ruff format --check . && uv run ruff check .`
  - `npx prettier@3.10.1 --check .`
- Models are never called in tests: a `FakeLLM`/`FakeEmbedder` implement the same protocols; `OllamaClient` is tested with httpx `MockTransport`.
- Rule tests (a `tests/rules/` suite QA runs on every ticket): no request leaves through anything but the outbound guard (all other hosts raise); page fetch to a non-ticked domain or a redirect to one is refused before connecting; every stored sentence has ≥1 citation; plan content cannot be updated (trigger test); no student name appears in any request captured by the fake transport; no host other than the configured Ollama/SearXNG is ever contacted by AI code.
- Pure modules (timetable, chunker, unit heuristic, budget/split arithmetic, domain matching) are unit-tested to ~90% coverage.
- Views are tested with Flask's test client (status, key text, `aria-*` attributes from the specs). UI tickets are reviewed against `design/screens/` by the designer; screenshots are taken by running `docker compose up` and opening the pages.

## Open questions
- Generation model and speed: `qwen3.5:9b` is a default until the Ollama server's RAM/GPU are known and a trial on real material is done; generation time targets follow from that.
- Saving teacher-approved web pages and SearXNG web search both reach the public internet (never an AI service). Both are off unless enabled per subject / in Settings.
- Scanned syllabus PDFs: no OCR in the MVP; the teacher types the units.
