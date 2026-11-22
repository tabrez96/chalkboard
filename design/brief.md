# Chalkboard
## Problem
A teacher who builds a course from scratch (syllabus, chapters, class-sized lessons) spends hours collecting material and fitting it to the class length. Generic AI lesson planners produce plausible text without showing where it came from, take no account of which sources the teacher trusts, and offer no controlled review: the teacher cannot easily tell which claims are grounded.
Evidence: this is the owner's own need (personal use). No external demand data was gathered, and none is claimed. Existing tools exist in the space (see Competition), which shows the problem is recognised, not that this approach is wanted.

## Users & buyer
- User: one teacher per account (initially the owner). Students are stored as records only; their names are never sent to the LLM.
- Buyer: the owner, for personal use, with a requirement of zero running cost and no external AI services. No other paying customers in scope.

## Competition
- Chalkie (chalkie.ai): AI lesson/slide generator, upload or URL as starting point.
- Common Planner (commonplanner.com): AI unit and lesson plans, with admin coaching.
- Planboard / Chalk.com: lesson planning and timetables, not source-grounded generation.
- Name clash: ChalkBoard AI (chalkboardai.org, adaptive quizzes for students) and Chalkboard AI (chalkboard-ai.com, curriculum alignment for school leadership) already use the name in education.
Gap: none of these enforces a teacher-approved source allowlist, requires a citation for every claim, or keeps every change as a new version. All are cloud services. That is the differentiator, together with fully local operation.

## Constraint: local only, no external resources
The owner wants all models to run on their own hardware and no external resources. Facts that shape the plan:
- Claude cannot run locally: its weights are not distributed, so any use of Claude goes through Anthropic's servers. It is therefore not part of this plan.
- Model serving: a separate server (a Proxmox host owned by the owner) runs Ollama. The app does not run models itself; it calls Ollama over its HTTP API. The Ollama base URL is configuration.
- Embeddings: a Qwen embedding model served by Ollama (decided by the owner). The exact model and size are for the Architect to choose and verify against current Ollama availability.
- Generation, verification and chat: also served by the same Ollama server through the same interface. The model is not yet chosen and depends on the server's RAM and GPU.
- The LLM and embedding clients sit behind one interface, so a different model or endpoint can be swapped in by configuration.
- Outbound requests from the app are limited to (a) the Ollama server on the owner's network and (b) fetching pages the teacher has approved as sources (allowlist), or any page in open mode. No request goes to an external AI service. Whether (b) counts as "external" for the owner is an open point.

## MVP scope
Milestones 1-5 from the idea, plus data deletion and backups from milestone 6.
- Auth (one teacher per account), students as records, subjects with class length, versioned drafts.
- Sources: allowlist or open mode, uploads, fetch, chunk, embed (Qwen embeddings via Ollama API). The server never fetches a disallowed domain. Uploads work with no internet access (the Ollama server must be reachable).
- Overview: generate, approve, request changes (creates a new version), citations.
- Chapters: teacher names the topic, chapter draft, split into sessions by class length (3-hour chapter with 1-hour class gives 3 sessions).
- Today view and assistant chat: daily lesson, its sources, grounded chat; answers outside the sources are labelled.
- Ollama backend behind one interface; no cloud backend in the MVP. Clear error state when the Ollama server is unreachable or a model is missing, and a settings check that tests the connection.
- Citation verification step: after generation, each claim is checked against its cited chunk and unsupported claims are flagged to the teacher. This is what makes a smaller local model acceptable.
- Hardening subset: backups, tests, data deletion. No cost cap is needed, since nothing is billed.
- Rules: every claim cites a source chunk; changes create new versions, never edits; student names never reach the LLM; no request to an external AI service.

## Out of scope
- Claude or any other hosted LLM (excluded by the owner's no-external-resources requirement).
- Running models inside the app's own containers (models live on the separate Ollama server).
- Post-class feedback and re-planning, handouts, holiday calendars (later).
- Multiple teachers paying, billing, per-teacher API keys.
- Student-facing access.

## Pricing hypothesis
None: personal use, self-hosted, no paid services. Revisit if other teachers are ever invited.

## Success metrics
- A teacher can go from an empty subject to an approved overview, a chapter and class-sized sessions without leaving the app, at zero cost and with no external AI call.
- 100% of generated claims carry a citation to a stored chunk; unsupported citations are flagged by the verification step.
- Zero fetches to disallowed domains; zero student names in LLM requests; zero outbound requests to AI services other than the configured Ollama server (checked by tests).
- Generation time per overview or chapter on the Ollama server is tolerable to the owner (target to be set once hardware is known).
- The owner uses it to prepare real lessons.

## Risks
- Model quality: small open-weight models are weaker at grounded, cited writing and at non-English material than Claude would be. Mitigated by the verification step, teacher approval of every step and the swappable backend. Not yet tested on the owner's material.
- Speed: the Proxmox server's RAM and GPU are unknown; without a GPU, long chapters and the extra verification calls may be slow, and hardware may rule out larger models.
- Dependency on a second machine: if the Ollama server is down or unreachable, generation, search and chat stop. Uploaded sources and approved drafts remain readable.
- Network exposure: Ollama's API has no authentication by default; it should be reachable only from the app host on the private network.
- Embedding model change: switching the embedding model later requires re-embedding all chunks, since vectors from different models are not comparable.
- Model download and updates on the Ollama server are manual.
- Name clash: two education products already use "Chalkboard AI". Fine for private use; a problem if ever published.
- Copyright: the owner confirmed the teacher is responsible for rights to uploaded material.
- Scope: five milestones is large for an MVP; the Today view and assistant could slip.

## Open points
- Ollama server RAM and GPU, and the Qwen embedding variant and generation model to use: a short trial on a sample of real material should precede committing.
- Language, subject and age group of the material: unspecified; language in particular affects model quality.
- Whether fetching teacher-approved web pages is acceptable under "no external resources", or whether sources should be uploads only.

## Runner-up ideas
None: this is the owner's own idea and no alternatives were explored.

## Added since approval
- After each class, a short trivia quiz for the teacher to ask students (added 2026-11-22)
- Assistant chat can also answer from the general web when the teacher switches to 'Sources + web'; web answers are labelled with their links (needs a web search service, ideally self-hosted) (added 2026-11-22)
- Light and dark theme, switchable from the sidebar and Settings (added 2026-11-22)
- Each subject selects one or several websites from a global allowed list for saving web pages as sources (added 2026-11-22)
- Each subject follows a curriculum board (CBSE, ICSE, a named state board, IB, or none); the board's syllabus PDF is added as a separate source, the course overview follows its units in order with each chapter citing its unit, and a coverage check flags uncovered units (added 2026-11-22)

