# Chalkboard: guide

## Getting started
1. Open Chalkboard in your web browser at the address where it is installed, and sign in with your email and password (S01). Chalkboard runs on your own computer, so there is nothing to download or pay for.
2. Open Settings, enter your Ollama server's address and the two models, and press **Test connection**. When it says the server answers and both models are installed, press **Save**. Here you can also pick Light, Dark or Match system, and add the websites you trust to **Allowed websites** (S08).
3. On Subjects, press **New subject** and enter its name, the class or grade and how long one class is. Choose the curriculum board it follows: CBSE, ICSE, a state board (type its name), IB, or Other or none. If it follows a board, add this year's official syllabus for the subject and grade under **Syllabus PDF** (you can also do this later). Pick the days it is taught and the time on each day, for example Tue and Thu at 09:00, and the term's first and last day. Chalkboard shows how many classes and hours of teaching that gives. Under Sources, choose **Uploads only**, or **External websites** and tick one or more of the allowed sites it may use (S02).
4. On Sources, the syllabus sits at the top of the list with a **Syllabus** tag; it sets the chapters and their order. If you skipped it, press **Add syllabus PDF**, or **Choose from sources** if it is already in the list (any source can be marked with **Use as syllabus**). Press **See units** to check its units, or **Type the units** if none were found. Then press **Upload files** for your other files (PDF, Word, text or Markdown), or paste a page link and press **Save page**. Wait until every source shows **Ready** (S03).
5. On Course overview, press **Generate**. The chapters follow your syllabus's units in order, and each chapter shows the unit it covers. Look at **Coverage**: any unit no chapter covers is flagged, and you can press **Ask to add it** or **Not this term**. Read the overview, open any citation number to see the passage behind a sentence, and look at every sentence marked **Check this**. Each chapter's hours are an estimate fitted to the term's teaching time, shown against the total. To change hours or anything else, type what should change in the box under the overview and press **Request changes**; you get a new version. Press **Approve** when it is right (S04).
6. If you want, add your students on Students, one by one or with **Import CSV**. Their names stay in Chalkboard and are never sent to the model (S07).

## How to use it
**Prepare the next chapter**
1. Open Chapters. The next chapter's topic is already taken from your approved overview; rename it or add notes on what to focus on if you like (S05).
2. Press **Write chapter draft**, then review it the same way as the overview: citation numbers, **Check this** sentences, and **Request changes** if needed (S05).
3. Press **Approve and split into classes**. Chalkboard divides the chapter by your class length, for example 3 hours at 60 minutes gives 3 classes, each with a timed lesson plan (S05).
4. Press **Open lesson plan** on any class to see it in full (S06).

**Teach today's class**
1. Open Today before class to read the lesson plan, its timed steps and the sources it uses, with page ranges (S06).
2. During class, ask the assistant a question. With **Your sources** it answers only from your sources and says **Not in your sources** when they don't cover it. Switch to **Sources + web** to let it search the web; those answers are labelled **From the web** with their links, so check them before using them in class (S06).
3. Use **Previous** and **Next class** to move between lessons. Lessons follow your timetable in order; if the term runs out before the chapters do, Subjects and Course overview tell you which classes won't fit, and you can ask for shorter chapters with **Request changes** on Course overview or move the term's last day in the subject's settings (S04, S02).

**Quiz the class afterwards**
1. When the class is over, the **Trivia for class** card on Today shows 3 to 5 questions, each with its answer and the citation of the passage it comes from (S06).
2. Press **Edit** to change a question yourself, or **New question** to replace just that one (S06).
3. Press **Copy for the board** to copy the questions without the answers, ready to put up for the class (S06).
