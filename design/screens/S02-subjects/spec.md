---
id: S02
name: Subjects
purpose: See every subject at a glance and create a new one
size: 1280x800
---

## Content and actions
- Sidebar: no subject is chosen on this page, so the switcher reads **Choose a subject** (grid icon, "3 subjects" or "No subjects yet") and the subject pages (Today, Course overview, Chapters, Sources, Students) are hidden. All subjects, Settings, the theme toggle and the Ollama status stay. Opening a subject card or picking one in the switcher brings the subject pages back.
- Greeting, **New subject** opens the subject form (`states/new-subject.html`). The same form opens as **Subject settings** from the gear on each card and from the subject switcher.
  - Name, class or grade, class length in minutes.
  - **Curriculum board**: CBSE, ICSE, State board (a name field appears, e.g. "Maharashtra State Board"), IB, or Other or none (a dropdown on its own row under name, grade and class length). With Other or none, S04 works from the sources alone.
  - **Syllabus PDF** (optional, beside the board; hidden with Other or none): **Upload PDF**, or **Choose from sources** (a list of the subject's existing sources; only in subject settings, since a new subject has none). Once picked, the field shows the file name with **Change** and a trash icon (`aria-label="Remove syllabus PDF"`). The file goes into Sources (S03) as that subject's syllabus, pinned at the top of the table. Removing it here only unmarks it; the file stays a source unless deleted on S03.
  - **When classes happen**: class days (Mon–Sat buttons, `aria-pressed`), a start time per chosen day (e.g. Tue 11:15, Fri 10:00), and the term's first and last day. A chip beside the term shows the result ("28 classes · 23 h 20 min", `role="status"`). That total is the course budget S04 plans against.
  - **Sources**: a two-way choice, **Uploads only** or **External websites** (segmented control, `aria-pressed`). Choosing External websites shows a checklist of the sites on the allowed list (S08), with real checkboxes in a labelled fieldset; the teacher ticks one or more. Saving with External websites and no site ticked shows "Tick at least one site, or choose Uploads only". **Edit allowed sites (Settings)** links to S08. With open mode on in S08, the checklist is replaced by the line "Any website is allowed".
  - The timetable is optional at creation; the footer says everything can be changed later.
- One card per subject: colour and initial, board, class and class length, timetable summary (days, times, term), progress (chapters ready), the next class. Clicking opens that subject's S06 Today.
- **Coming up**: classes in the next 7 days across subjects; a row opens the lesson on S06.
- Dates come from the timetable: lessons are placed on the subject's class days in order (chapter 1 class 1 on the first class of the term, and so on). Next class, Coming up and S06 Today all read from that placement.

## States
- Main: `screen.html` (Physics, Mathematics, Chemistry).
- Empty / first run: `states/empty.html`, one short card with **New subject**.
- Dark theme: `states/dark.html`.
- Subject form: `states/new-subject.html` (Chemistry, CBSE with its syllabus PDF picked, Mon and Wed 10:00, 23 Nov – 26 Feb).
- No timetable yet (Chemistry card in `screen.html`): the card says "No timetable yet" and Next class offers **Add class times**; the subject is left out of Coming up. S04 shows hours without a budget, S06 asks for class days.
- Term runs out before the chapters (Mathematics card in `screen.html`): amber chip "Chapter 6 won't fit the term", linking to S04, where the over-budget state explains it.

## Notes and open questions
- Subject colour is picked automatically from a fixed set of six and can be changed later; it only marks the subject, it carries no meaning.

- Holidays and skipped dates are out of scope for now (a later milestone), so the term is every chosen weekday between the first and last day. Until then, a missed class is absorbed through Request changes on S04, which writes a new version of the overview.
- Changing the board later is allowed: the Syllabus PDF field empties (the old file stays a source), S03 asks for the new board's syllabus, and S04 suggests Request changes so the overview follows it. The existing approved version is kept.
- Changing the timetable or term later moves the dates of every lesson not yet taught and changes S04's budget; it never rewrites content.

## Change log
- First version, from the approved brief.
- Redesigned: modern look (Raleway headings, Inter body, new palette), light and dark themes, sample teacher Aamina and real subjects (Mathematics, Physics, Chemistry), sidebar with subject switcher instead of a "This subject" section. Requested in UX review.
- Timetable: class days and times and term dates in the subject form (new and settings), dates for Next class, Coming up and Today taken from it, no-timetable and term-runs-out states; website access choice added to the form; empty state now leads to the form. Requested in UX review.
- Curriculum board: board choice (CBSE, ICSE, State board with its name, IB, Other or none) in the subject form; board shown on each subject card. Requested in UX review.
- Simplicity pass requested in UX review: site choice reduced to Uploads only / Uploads and websites with site chips; board is a dropdown; form laid out as labelled rows with less helper text; result shown as a chip; Coming up rows show the lesson title only; cards drop the separate status line; shorter empty state.
- Websites choice is two options, Uploads only or External websites; External websites shows a checklist of the sites allowed in S08 (tick one or more), replacing the site chips. Requested in UX review.
- Subject cards simplified because lines wrapped: board and grade on one line, class days and length on the next (term dates stay in subject settings); Next class is a small label, then the date, then the lesson; shorter term-runs-out chip; sidebar switcher shows "Grade 11 · 60 min". Requested in UX review.
- Syllabus PDF field beside the curriculum board (upload, or choose one of the subject's sources); it goes into Sources marked as the syllabus. Sidebar on this page reads "Choose a subject" with no subject selected and hides the subject pages. Collapsible sidebar. Requested in UX review.
