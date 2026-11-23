---
name: ui-review
description: Review a web page or app screen for design and usability problems, with screenshots, and fix them. Use when the user asks to review, critique, polish or fix how a UI looks.
---

# Review a UI

Method adapted from the open Taste Skill (MIT, github.com/Leonxlnx/taste-skill).

1. Open it. For a file or address run `ui_audit` (wide, tablet, phone, light and dark). For a screenshot or other file run `view_output`. If it needs a sign-in or clicks, use the browser tools, then `browser_screenshot`.
1b. Also run `ui_smoke` on a page that has controls: it clicks them, submits the forms with bad input and reports what does nothing or breaks.
2. Read the report and look at the pictures. If you cannot see images, use the layout outline and the text the tools read out of each picture.
3. Judge it in this order: does it work (nothing cut off, no sideways scroll, no errors); can it be read (contrast, size, line length); is the hierarchy clear (one main thing per view, one primary action); is the spacing even; does it hold at 390 px; do keyboard focus and states exist.
4. List the problems, worst first, each with where it is and the fix. Keep to what you saw. No general advice.
5. If the user asked you to fix it, change the code, run `ui_audit` again and repeat until it says "No problems found". Then reply with the before and after screenshot paths and a short list of what changed.
