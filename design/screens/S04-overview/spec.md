---
id: S04
name: Course overview
purpose: Review and approve the course map: chapters, hours and what each covers
size: 1280x800
---

## Content and actions
- The overview is the map of the course, not the class content: chapters in order, hours each (and so the number of classes), and a few sentences on what each covers. Class content is written per chapter on S05 and taught from S06.
- Summary at the top, two columns with a small label each: **Syllabus** (when the subject follows a board, S02): chip "CBSE · Class 11 · 5 units" with the syllabus file below it (opens its row on S03); **Course budget**: chip "16 h of 16 h planned" with classes, days and term dates below it. The budget bar runs full width under both. The overview's chapters follow the syllabus units in the syllabus's order; the other sources supply each chapter's content.
- Each chapter cites the syllabus unit it covers with a **Syllabus · Unit N** chip in the small line above its title; clicking it opens the unit's text from the syllabus in the right panel, like a citation number. A chapter may cover part of a unit, or one unit may span two chapters; each chapter still names its unit.
- **Coverage** (right column): every unit as one row (unit number, name, chapter chip). An uncovered unit is an amber block: "Unit N" with a "Not covered" chip, then its name, then **Ask to add it** (prefills Request changes, e.g. "Add a chapter for Unit 1 before Chapter 1") and **Not this term** (marks the unit as taught in another term; it stays listed as "Not this term" and is no longer flagged).
- **Course budget**: the term's teaching time from the S02 timetable (classes × class length, days, term dates). The overview spreads this budget across the chapters when it is written.
- Each chapter's hours are read-only and labelled **Estimate**: the overview fits them to the term's teaching time when it is written, and each shows its class count (hours ÷ class length, rounded up). To change hours, the teacher uses **Request changes** (e.g. "Make chapter 2 shorter"), which writes the next version.
- Every sentence ends with one or more citation numbers (one per source it draws on, so a sentence can cite several sites). Clicking a number opens the passage in the right panel.
- **Check this** marks a sentence whose cited passage doesn't say what the sentence says (found by the check step after writing). The sentence is highlighted, its citation turns amber, and the panel explains the mismatch, with **Ask to fix this sentence** (prefills the change request).
- Banner with the number of sentences to check and **Show it** (jumps to the first). **Request changes** (writes the next version, keeps this one), **Approve version N**. **Versions** opens the list; older versions are read-only.
- Right column: the passage panel and Coverage. Uncovered units carry **Ask to add it** and **Not this term** inside the coverage list (no separate banner).
- **Request changes** box: a labelled multi-line text area ("What should change?"); below it, on its own line, "This writes version N. The current version is kept." on the left and **Request changes** and **Approve version N** on the right.

## States
- Sidebar collapsed: `states/sidebar-collapsed.html` (icon rail: logo mark and expand toggle, subject initial, one icon per page, footer icons; the Chapters icon shows its tooltip on focus).
- Main: `screen.html`, version 3 draft with one sentence to check; CBSE Class 11 syllabus with 5 units, Unit 1 (Units and measurements) not covered by any chapter.
- Generating: `states/generating.html`, three steps (find passages, write, check every sentence); the teacher can leave the page.
- No sources: `states/no-sources.html`, explains what the overview is, links to S03.
- Dark theme: `states/dark.html`.
- Board chosen, no syllabus yet: `states/no-syllabus.html`. The Syllabus line shows "Not added yet", says the chapters come from the sources alone with no coverage check, and links to S03. Generate stays available. No unit chips, no coverage card.
- Board set to Other or none: as `states/no-syllabus.html` without the Syllabus line: the overview works from the sources alone.
- Over budget: `states/over-budget.html`, 18 h of 16 h (the term was shortened in S02 after this version was written, or the overview could not fit the budget). Amber bar and chip, an alert naming what won't fit ("the last 2 classes of Chapter 4") with **Subject settings** (S02) to move the term's last day; the chapter that spills over shows "2 classes after the term ends". Approving is still allowed. Under budget shows "14 h of 16 h planned · 2 h free" in the same place.
- No timetable: `states/no-timetable.html`, chip "No timetable yet", hours are estimates only, link **Add class times** (S02).
- Ollama unreachable: S03's banner; Generate and Request changes are disabled.
- Approved: chip "Approved · version N"; the approved chapters appear on S05.

## Notes and open questions
- Layout rule for list rows: at most two items on a line. Labels and chips go in a small line above the title; numbers go on the right, with their unit underneath.
- The syllabus sets structure and order only; sentences still cite the subject's sources, never the syllabus, so every claim stays traceable to teaching material.
- Approving with an uncovered unit is allowed; the flag stays on the version.
- A syllabus covering the whole year often spans several terms: **Not this term** keeps the coverage check honest without forcing every unit into one term's budget.
- Approving with sentences still to check is allowed; the chip stays on them.
- A sentence with no citation is never shown: the writer must retry or drop it.
- "Not supported" was renamed "Check this", with the reason spelled out, because the old label was unclear.

- Hours are not editable on S04: every change to the map, hours included, goes through Request changes and writes a new version, so the versions stay the single record of the plan. The over-budget alert points to Request changes or to the term dates in S02.

## Change log
- First version, from the approved brief.
- Redesigned: modern look (Raleway headings, Inter body, new palette), light and dark themes, sample teacher Aamina and real subjects (Mathematics, Physics, Chemistry), sidebar with subject switcher instead of a "This subject" section. Requested in UX review.
- Citation per sentence, several sources per sentence; "Not supported" renamed "Check this" with the reason shown.
- Course budget from the term timetable, chapter hours labelled as estimates and editable in place with the class count updating at once; over-budget and no-timetable states. Requested in UX review.
- Chapter hours made read-only: the inline stepper and the "Set by you" chip are removed; hours show as estimates fitted to the term budget, changed only through Request changes. Over-budget alert copy updated. Requested in UX review.
- Curriculum board: overview follows the syllabus's units in order, Syllabus line at the top, a Syllabus · Unit N citation on each chapter, a coverage card and banner flagging uncovered units, no-syllabus state. Hours stay read-only estimates. Requested in UX review.
- Simplicity pass requested in UX review: request box is a text area with the buttons on a new line; uncovered-unit actions moved into the coverage card (banner removed); "Sources in this version" card removed; shorter banner and budget copy.
- Chapter rows restructured because the header line was crowded (chapter number, title, unit chip, Estimate chip, hours and classes on one line): a small line with "Chapter N" and the unit chip, the title on its own line, and hours on the right with "N classes · Estimate" under them. Summary at the top split into two labelled columns; coverage list rows simplified and the uncovered unit stacked. Requested in UX review.
- Collapsible sidebar: a toggle at the top collapses it to an icon rail; collapsed state in `states/sidebar-collapsed.html`. The syllabus file link now opens its row on S03, where the syllabus is a source. Requested in UX review.
