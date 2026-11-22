---
id: S06
name: Today
purpose: Teach today's lesson from its plan, with its sources and an assistant
size: 1280x800
---

## Content and actions
- Header: the small line above the title reads subject, date, chapter and class number ("Physics · Tuesday 24 November · Chapter 1, class 2 of 3"); lesson title; previous / next arrow buttons (labelled "Previous class" and "Next class") to move between lessons (S05's **Open lesson plan** lands here on any class).
- Lesson plan: goal, then timed steps with what to say and do, each sentence with citations.
- Sources for this lesson (labelled "Sources") as one row of numbered items with page ranges; a number opens the passage.
- Assistant: **Your sources** / **Sources + web** switch.
  - Your sources: answers carry "From your sources" and citations; if the sources don't cover it, the answer is labelled "Not in your sources" (dashed amber border) and suggests switching.
  - Sources + web: answers from the web carry "From the web" with their links and "check before using in class". Source-based answers keep their own label.
- Student names typed in the chat are removed before sending.
- **Trivia for class** (header button, and the card shown once the class time has passed): 3 to 5 questions to ask the class after the lesson. Each shows its answer and the citation number of the passage it comes from. **Edit** changes a question by hand (marked "Edited"); **New question** writes a replacement for that one question only, from the same lesson's sources. **Copy for the board** copies the questions without answers. Student names are never shown in the card or sent to the model.

## States
- Main: `screen.html`, sources only.
- Web: `states/web.html`, Sources + web.
- Dark theme: `states/dark.html`.
- After class: `states/trivia.html`, plan collapsed, trivia card with 4 questions.
- Trivia not written yet: `states/empty.html`, choose 3, 4 or 5 questions and **Write trivia**. While writing: progress line with `role="status"`; Ollama unreachable: the button is disabled with S03's banner.
- No lesson today: `states/no-lesson.html`, offers the next class.
- No timetable: `states/no-timetable.html`, asks for class days (**Add class times**, S02 subject form) and offers the lessons on S05.
- After the term's last class: Today says "The term is over", how many lessons were not taught, and offers **Subject settings** (S02) to move the term's last day. Same layout as `states/no-timetable.html`.
- Waiting: typing dots. Ollama unreachable: chat disabled with S03's banner; the plan stays readable. Web search off in S08: the switch shows "Web search is off in Settings".

## Notes and open questions
- Web answers need a search service reachable from the app; which one (ideally self-hosted) is for the architecture. It is the only part of the assistant that goes beyond the allowed sites, and it is off unless the teacher picks it.

- Which lesson is today comes from the timetable on S02: lessons fill the subject's class days in order.

## Change log
- First version, from the approved brief.
- Redesigned: modern look (Raleway headings, Inter body, new palette), light and dark themes, sample teacher Aamina and real subjects (Mathematics, Physics, Chemistry), sidebar with subject switcher instead of a "This subject" section. Requested in UX review.
- Assistant scope switch: your sources, or sources plus the web; added web and dark states.
- Trivia for class card after each class (answers, citations, edit, new question) with its empty state. Requested in UX review.
- Today's lesson is placed by the subject timetable; added the no-timetable state. Requested in UX review.
- Simplicity pass requested in UX review: Previous and Next class are arrow icon buttons.
- Header chip "Chapter 1 · Class 2 of 3 · 60 min" moved into the line above the title, so the header holds only actions; sources strip has its label above the items so nothing wraps. Requested in UX review.
- Collapsible sidebar: a toggle at the top collapses it to an icon rail with tooltips; the choice is remembered. Requested in UX review.
