---
id: S05
name: Chapters and classes
purpose: Write, approve and split each chapter into class-length lessons
size: 1280x800
---

## Content and actions
- Left: the chapters of the approved overview, in order, with status. The next chapter's topic comes from the overview, so the teacher doesn't have to type it; they can rename it, add notes on focus, or **Add a chapter** the overview missed.
- Approved chapter (main): banner with the split arithmetic (3 h at 60 min = 3 classes), one card per class with its timed outline and citations, **Open lesson plan** (S06 for that class). **Read the chapter**, **Request changes** (new version, re-split after approval).
- Next chapter card: **Write chapter draft** or, if a draft exists, **Review draft**.
- Draft chapter: same review pattern as S04 (citations, Check this, request changes) with **Approve and split into N classes**.

## States
- Main: `screen.html`, Chapter 1 approved, 3 classes.
- Draft: `states/draft.html`, Chapter 2 version 1 with 2 sentences to check.
- Generating and Ollama unreachable: as S04.
- No approved overview: list empty with "Approve the course overview first" and a link to S04.

## Notes and open questions
- A remainder becomes a shorter last class, labelled with its minutes.
- The teacher still confirms each topic (the brief's rule), but it is suggested from the overview instead of typed from scratch.

## Change log
- First version, from the approved brief.
- Redesigned: modern look (Raleway headings, Inter body, new palette), light and dark themes, sample teacher Aamina and real subjects (Mathematics, Physics, Chemistry), sidebar with subject switcher instead of a "This subject" section. Requested in UX review.
- Next topic suggested from the approved overview instead of typed; sessions renamed classes and link to their lesson plan.
- Simplicity pass requested in UX review: class-count banner replaced by one line; helper texts removed from the chapter list and next-chapter card; the draft's request box matches S04 (text area, buttons on a new line).
- Sidebar subject switcher reads "Grade 11 · 60 min" so it fits on one line. Requested in UX review.
- Collapsible sidebar: a toggle at the top collapses it to an icon rail with tooltips; the choice is remembered. Requested in UX review.
