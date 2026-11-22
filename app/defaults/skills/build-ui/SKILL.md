---
name: build-ui
description: Build a web page, landing page, dashboard or app screen that looks designed, and check it in a real browser before saying it is done. Use whenever the user asks for a page, site, UI, screen, component or prototype.
---

# Build a UI

Method adapted from the open Taste Skill (MIT, github.com/Leonxlnx/taste-skill). A UI is not done when the code runs. It is done when you have looked at it.

1. Decide the kind of thing: marketing page, dashboard or tool, form, or app screen. Ask one question only if you cannot tell who it is for. Marketing pages sell one idea. Tools put the data and the next action first.
2. List the screens or sections before writing code. Real copy, no lorem ipsum: use the user's words, files and numbers. Never invent customers, logos, quotes or statistics; leave them out. A fact you do not have (hours, prices, a phone number) is left off the page and asked for in your reply. Notes to the owner never go on the page, and neither does filler text or a placeholder; if a link needs a value you lack, leave that button out.
3. Start from `starter.html` in this skill's folder (copy it to the project, then change it). It already has the design system below, light and dark, and passes `ui_audit`. Keep one file with inline CSS unless the user wants a framework.
4. One design system, declared once as CSS variables: colours (background, surface, text, muted text, one accent, border), a type scale, spacing steps of 4 or 8 px, one radius, one shadow. Use only those. One accent colour. Two font weights. System font stack unless the user gives a font.
5. Build mobile first, then widen. Layout with grid and flex, text in `rem`, content width capped near 70 characters for reading and 1100px for pages. No fixed widths over 100%.
6. Look at it. Run `ui_audit` on the file. It checks a wide screen, a tablet and a phone in light and dark, lists what is broken and saves screenshots. Then run `view_output` on the saved screenshots if you can see images, and read the layout outline if you cannot. Fix every MUST FIX item, then run it again. Repeat until it says "No problems found". Do not tell the user it is finished before that.
7. Review it yourself against the house rules below, change what fails, run `ui_audit` once more.
8. Reply with the file path, what you built in two sentences, and the screenshot paths.

House rules
- Text contrast at least 4.5 to 1 (3 to 1 for large text). Muted text still passes.
- Every button and link is at least 44 by 44 px on a phone. Every icon button has an aria-label. Every input has a label. Every image has alt text or is marked decorative.
- Hierarchy: one h1, headings in order, one primary button per view. The most important thing is the biggest and highest.
- Spacing: generous and even. Sections 64 to 96 px apart on desktop, 40 to 56 on a phone. Related things closer than unrelated things.
- States: hover, focus (a visible ring, never removed), disabled, loading, empty and error states exist for anything interactive.
- Motion: only to explain a change, under 200 ms, and off when `prefers-reduced-motion` is set.
- Dark mode follows `prefers-color-scheme` and is designed, not inverted: lower contrast surfaces, no pure black.
- Copy: short, concrete, in plain words. Buttons say what happens ("Start free trial", not "Submit"). No exclamation marks, no em dashes.

Never
- Centre-aligned walls of text, purple-to-blue gradients on everything, glowing blobs, emoji used as icons, three identical cards with an icon and a headline, "Unleash", "Seamless", "Elevate", fake testimonials, stock-photo placeholders, a hero with no action.
- Text over an image without a scrim, tiny grey text, carousels that hide the content, modals on page load.
- Fixed pixel layouts, horizontal scroll, clicking-only interactions with no keyboard path.

Dashboards and tools: lead with the numbers or the table, not a banner. Use a table with aligned numbers, sortable headers and a real empty state. Charts get a title that states the takeaway, labelled axes and no more than five colours. A chart is made with inline SVG or the browser's canvas and checked with `view_output`.

Apps with a framework (React, Vue, Svelte): build, serve it on a local port with bash_start, then run `ui_audit` on `http://127.0.0.1:PORT/`. Stop the server with bash_stop when done.
