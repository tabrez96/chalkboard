---
id: S03
name: Sources
purpose: Add, review and remove the sources a subject's plans are written from
size: 1280x800
---

## Content and actions
- **Add bar** (one row): **Upload files** (PDF, Word, text, Markdown, up to 50 MB; files can also be dropped anywhere on the page), or paste a page link and press **Save page**: Chalkboard downloads the page once, keeps its text, and treats it like an uploaded file. It never re-fetches on its own.
- Under the add bar, one small read-only line names the subject's sites ("Pages from openstax.org, khanacademy.org · Change"); **Change** opens subject settings on S02. A page can be saved only from those sites. Single column.
- The link field and **Save page** show only when the subject allows at least one site; with Uploads only, the add bar offers files alone.
- A pasted address from an unticked site shows "This site isn't chosen for Physics" with **Change in subject settings** (S02), or a link to S08 if it isn't on the allowed list; the server never requests it until then.
- **Source table**: the syllabus is a source like any other, marked and pinned.
  - **Syllabus row** (only when the subject follows a board on S02 and has a syllabus): pinned at the top, tinted primary soft with square ends and a straight 3px primary bar down the full height of its left edge (with the Syllabus chip). Small line above the name: **Syllabus** chip and the board and grade ("CBSE · Class 11"); then the file name (opens its text). Status "Ready · N units". Row actions: **See units** (the unit list read from the file, in order) and a trash icon (`aria-label="Delete the syllabus"`). It has a file icon instead of a source number: S04 cites it as "Syllabus · Unit N", and sentences never cite it.
  - **Other rows**: number, name (opens the extracted text), from (site or Uploaded), status chip (Ready · N passages / Reading N% / Saved · waiting for Ollama / Blocked: site not chosen / Couldn't read the file), and two icon buttons with tooltips: **Use as syllabus** (bookmark icon, `aria-label="Use source N as syllabus"`) and **Delete** (trash icon, `aria-label="Delete source N"`).
  - **Use as syllabus**: one syllabus per subject. When there is none, the source becomes the syllabus at once and moves to the top. When there is one, a confirmation asks "Use this as the syllabus? *CBSE Physics Class 11 syllabus 2026–27.pdf* goes back to being a normal source." The old syllabus keeps its passages and takes a source number again.
  - The syllabus can also be set from the subject form on S02 (**Syllabus PDF**: upload, or choose one of the subject's sources); it arrives here marked as the syllabus.
- The syllabus decides the overview's chapters and their order (S04); the other sources supply the content.

## States
- Main: `screen.html` (Physics, CBSE Class 11 syllabus row pinned, 5 units found, 4 teaching sources).
- Board chosen, no syllabus yet: `states/no-syllabus.html`. A dashed amber line above the table: "No CBSE syllabus yet. The overview is planned from your sources alone, with no coverage check." with **Add syllabus PDF** (upload) and **Choose from sources** (pick a row to mark). The table has no pinned row.
- Syllabus read but no units found (e.g. a scanned PDF): the syllabus row's status reads "No units found" and **See units** becomes **Type the units** (a short list, one per line).
- Other or none on S02: no syllabus row, no line, and **Use as syllabus** is hidden.
- Empty: `states/empty.html` (no sources yet; the no-syllabus line offers **Add syllabus PDF** only).
- Ollama unreachable: `states/ollama-down.html`; files are saved and queued as "waiting for Ollama".
- Open mode (S08 "Allow any website" on): the line reads "Any website is allowed" with a link to S08.
- Deleting a source used by an approved plan asks for confirmation and lists the plans whose citations will show "source deleted". Deleting the syllabus asks too, and S04 then shows its no-syllabus state.

## Notes and open questions
- Changing the syllabus (Use as syllabus on another row, or a new upload) keeps the approved overview as it is; S04 then shows the coverage check against the new units and suggests Request changes.
- Source numbers (1, 2, 3…) are stable per subject and are the numbers on every citation.
- A subject can draw on many sites at once; the allowed list is global, the choice is per subject and made on S02 (Uploads only, or External websites with the ticked sites). With Uploads only, the line reads "Uploads only · Change".

## Change log
- First version, from the approved brief.
- Redesigned: modern look (Raleway headings, Inter body, new palette), light and dark themes, sample teacher Aamina and real subjects (Mathematics, Physics, Chemistry), sidebar with subject switcher instead of a "This subject" section. Requested in UX review.
- Subjects pick one or several sites from the allowed list; "Fetch page" renamed "Save a web page" with an explanation.
- Website choice moved to the subject form on S02; this screen shows it read-only, and Save a web page appears only when a site is allowed. Requested in UX review.
- Curriculum board: Syllabus card at the top, separate from other sources, with a no-syllabus state (`states/no-syllabus.html`) explaining what it means. Requested in UX review.
- Simplicity pass requested in UX review: single column; the add card is one row (Upload files, link field, Save page); website choice is one line under it; Delete is a trash icon; "How sources are used" card removed; shorter no-syllabus copy.
- Notes updated for the S02 websites choice (Uploads only or External websites with a checklist); the read-only line shows the ticked sites, or "Uploads only". Requested in UX review.
- Sidebar subject switcher reads "Grade 11 · 60 min" so it fits on one line. Requested in UX review.
- The syllabus is now a source: the separate Syllabus card is removed; the syllabus is a pinned row in the table with a Syllabus chip; any source can be set as the syllabus with the row action Use as syllabus (one per subject, a new one replaces the old); the no-syllabus prompt is a line above the table with Add syllabus PDF and Choose from sources; Delete icons labelled "Delete source N". Requested in UX review.
- Collapsible sidebar (toggle at the top, icon rail with labels as tooltips). Requested in UX review.
- Syllabus row: removed the primary left rule, which curved at the rounded corners; the row is marked by its primary soft fill and the Syllabus chip. Checked the other screens: no other rounded element carries a left rule (the passage quote rule is square). Requested in UX review.
- Syllabus row: the primary left rule is back as a straight 3px bar the full height of the row; the row's ends are now square so the bar never curves, and it keeps its primary soft tint (main screen and `states/empty.html`, `states/no-syllabus.html`, `states/ollama-down.html`). Checked S01–S09 for other curved left accents on rows, chips and panels: none found (the passage quote rule on its square panel is already straight). Screens changed: S03 only. Requested in UX review.
