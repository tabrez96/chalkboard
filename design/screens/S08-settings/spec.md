---
id: S08
name: Settings
purpose: Connect the Ollama server, choose allowed websites, theme, backups and deletion
size: 1280x800
---

## Content and actions
- Sidebar: Settings is an account page. Reached from S02 (as in `screen.html`), the switcher reads **Choose a subject** and the subject pages are hidden. Reached from a subject page, the switcher keeps that subject and its pages stay, so the teacher can go straight back.
- **Ollama server**: address, embedding model, writing model, **Test connection** (server answers and both models are installed), **Save**.
- **Appearance**: Light / Dark / Match system (the sidebar toggle sets the same preference).
- **Backups**: last backup, **Back up now**, **Download latest**.
- **Delete data**: one subject (sources, plans, students) or everything (type DELETE to confirm).
- **Allowed websites**: the global list, with which subjects use each; Add, and a trash icon to remove each site. **Allow any website** (open mode), off by default.
- **Assistant may search the web**: allows the "Sources + web" choice on S06.

## States
- Main: `screen.html`, connected.
- Connection failed: `states/connection-failed.html`.
- Model missing: warning naming the model and the `ollama pull <model>` command to run on the server.
- Changing the embedding model asks for confirmation and re-reads all sources (progress on S03).

## Notes and open questions
- Model names shown are examples; the architecture picks the defaults.

## Change log
- First version, from the approved brief.
- Redesigned: modern look (Raleway headings, Inter body, new palette), light and dark themes, sample teacher Aamina and real subjects (Mathematics, Physics, Chemistry), sidebar with subject switcher instead of a "This subject" section. Requested in UX review.
- Added Appearance, global allowed websites list with open mode, and the assistant web search setting.
- Simplicity pass requested in UX review: Remove is a trash icon; allowed-sites copy now says subjects choose from it in their settings (S02).
- Allowed websites: the subjects using each site sit under the site name instead of beside it, so long lists no longer wrap. Requested in UX review.
- Sidebar matches the subject context: reached from S02 it reads "Choose a subject" with the subject pages hidden; reached from a subject it keeps that subject. Collapsible sidebar. Requested in UX review.
