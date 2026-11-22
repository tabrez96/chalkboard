---
id: S01
name: Sign in
purpose: Sign in to the teacher's account
size: 1280x800
---

## Content and actions
- Left: brand panel (logo, one-line promise, a sample of cited sentences). Right: email, password, **Sign in** (goes to S06 Today of the last subject, or S02 if there is none).
- Password reset is done on the server (`flask reset-password`); there is no email service.

## States
- Main: `screen.html`.
- Wrong email or password: `states/error.html`, alert above the form, password field marked invalid and focused.
- Loading: the button shows "Signing in…" and is disabled.

## Notes and open questions
- One teacher per account; no sign-up link (accounts are created on the server).

## Change log
- First version, from the approved brief.
- Redesigned: modern look (Raleway headings, Inter body, new palette), light and dark themes, sample teacher Aamina and real subjects (Mathematics, Physics, Chemistry), sidebar with subject switcher instead of a "This subject" section. Requested in UX review.
