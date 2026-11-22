# Chalkboard

Teacher-approved sources in, cited course plans out

A teacher who builds a course from scratch (syllabus, chapters, class-sized lessons) spends hours collecting material and fitting it to the class length. Generic AI lesson planners produce plausible text without showing where it came from, take no account of which sources the teacher trusts, and offer no controlled review: the teacher cannot easily tell which claims are grounded. Evidence: this is the owner's own need (personal use). No external demand data was gathered, and none is claimed.

## Where things are
- `design/` is the design library: `brief.md`, `ux.md`, and `screens/S01-name/` (`screen.png` + `spec.md`, plus
  `states/*.png` for empty, error and other states). Screens have permanent IDs (S01, S02, ...). Tickets name the
  screens they build by ID. **Read the spec and look at the screenshot of every screen you build**; `ux.md` has the
  colors, type and spacing. Don't edit `design/`; the designer owns it.
- Design canvas (private and behind a login, so you can't open it; it's where the design gets reviewed): <!-- canvas -->not published yet<!-- /canvas -->
- Source code lives outside `design/`. Put it where the stack below says.

## Stack
<!-- stack -->
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
<!-- /stack -->

## How we work here
- Two long-lived branches: `develop` is where the work lands (every ticket's PR goes there); `master` is the
  release branch and only moves when the maintainer launches a release. Never commit to `master`.
- One ticket at a time, on its own branch (`dmi/t-<n>`), cut from `develop`. Small, focused changes.
- Each ticket becomes a pull request into `develop` that the maintainer reviews. Aim for a pull request to change
  **under about 500 lines** (code and tests; lockfiles and generated files don't count), so it's easy to review. It's
  a target, not a hard limit: finish the work, and split it into more tickets only when it's far bigger.
- Commit messages and pull request titles say only what the change does (`Show the running meeting under Now`): no
  ticket number and no product name.
- Prefer boring, proven tools. Don't add a dependency unless the ticket needs it.
- Keep platform-specific code thin and put logic in pure code with unit tests (aim for about 90% coverage). Code
  that can't run in the sandbox is verified by review plus those tests, and then by hand by the maintainer.
- Format with the formatter in the stack table before you say you're done, and keep its check in CI and next to the
  test commands. Never format files your change doesn't touch.
- Write tests for what you build, and run them before you say you're done. Document the build and test commands in the README under "Development".
- Never commit secrets, tokens or personal data. Don't put real credentials in examples.
- If the ticket is unclear or the design is missing something, ask the architect or designer instead of guessing.
