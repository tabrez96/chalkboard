---
id: S09
name: Teacher guide
purpose: A one-page guide a teacher can be sent or print: first-time setup and a normal teaching day
size: 1280x1080
---

## Content and actions
- Header: mark, "How Chalkboard works", **Copy link** (copies the guide's address), **Print or save as PDF** (opens the browser's print dialog; both buttons are hidden in print).
- Intro: what Chalkboard does in two sentences.
- **First-time setup** (once, about 30 minutes), eight numbered steps, each naming the screen it happens on: sign in (S01), test the Ollama server (S08), pick allowed websites (S08), create a subject with class length (S02), tick its websites, add sources and wait for Ready (S03), generate and approve the overview (S04), write the first chapter and split it into classes (S05), add students, optional (S07).
- **A normal teaching day**, three cards: before class, read today's plan (S06); in class, teach with the assistant and its two scopes (S06); after class, the trivia (S06) and Next class.
- **When a chapter's classes run out** (every week or two, S05): the chapter loop as five steps.
- **What the labels mean**: citation number, Check this, From your sources, Not in your sources, From the web, "This writes version N". Same components as the app.
- **If something goes wrong**: Ollama server unreachable, and the student-name rule.

## States
- Main: `screen.html`. A static page with a single state; no data from the account.

## Notes and open questions
- Readable without signing in (a fixed page at `/guide`), so it can be sent to a teacher before their account exists. It contains no account data.
- Linked from S01 below the sign-in form ("New here? See how Chalkboard works") and from Settings (S08). Those links are added to S01 and S08 when this page is approved.
- Follows the account's theme when signed in, the system theme otherwise; print always uses the light theme on white.
- Time estimates are guesses until generation time on the Ollama server is known; update them then.

## Change log
- First version: a shareable guide to first-time setup and the daily workflow. Requested in UX review.
- Subject step now mentions class days, times and term dates. Follows the timetable change. Sources step and overview step updated for per-subject websites and editable hours.
- Overview step: chapter hours are read-only estimates, changed through Request changes. Follows the S04 change.
- Curriculum board: setup steps now cover choosing the board (S02), adding the syllabus (S03) and the syllabus coverage check (S04). Requested in UX review.
- Step 6 names the S04 card "Coverage", matching its new title. Requested in UX review.
- Setup steps updated: the syllabus PDF can be added in the subject form (step 4) and is a source marked as the syllabus on Sources (step 5). Requested in UX review.
