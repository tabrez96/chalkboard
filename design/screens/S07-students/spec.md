---
id: S07
name: Students
purpose: Keep the class list for a subject
size: 1280x800
---

## Content and actions
- Name-protection note. Table of students (initials, name, note), a pencil icon button per row (`aria-label="Edit <name>"`), **Add student**, **Import CSV** (name, optional note).
- Students are records only; they never sign in.

## States
- Main: `screen.html`.
- Empty: `states/empty.html`.

## Notes and open questions
- Names never reach the Ollama server; the assistant strips them from questions.

## Change log
- First version, from the approved brief.
- Redesigned: modern look (Raleway headings, Inter body, new palette), light and dark themes, sample teacher Aamina and real subjects (Mathematics, Physics, Chemistry), sidebar with subject switcher instead of a "This subject" section. Requested in UX review.
- Simplicity pass requested in UX review: Edit is a pencil icon; shorter name-protection note.
- Sidebar subject switcher reads "Grade 11 · 60 min" so it fits on one line. Requested in UX review.
- Collapsible sidebar: a toggle at the top collapses it to an icon rail with tooltips; the choice is remembered. Requested in UX review.
