---
name: build-website
description: Build a complete website or landing page (one or several pages, a menu, a contact form) and check it in a browser. Use when the user asks for a website, a landing page, a site for their business, a portfolio or a multi-page site.
---

# Build website

1. Read the build-ui skill first and copy its starter.html as the base of every page.
2. Ask one question only if you do not know what the business or person does. Use the user's real name, words and files; leave out anything you would have to invent (prices, hours, addresses, reviews, team names, phone numbers). For a vague request such as "landing page for my studio", build it with the sections that need no invented facts, put plainly marked fields in a small CONTENT-TODO list in your reply, and never on the page.
3. A landing page has, in order: a header with the name and a working menu (links to real section ids, a menu button on a phone that opens and closes with keyboard and Escape), a hero with one clear action, what it is and who it is for, how it works or what is included, proof only if the user gave it, pricing or next step, questions (details/summary), a final call to action, a footer. Every button and link goes somewhere real: a section, a page, mailto:, or a form. No "#" links.
4. Multi-page site: index.html plus one file per page, a shared style.css and the same header and footer on every page, the current page marked in the menu (aria-current). Add a title, meta description and Open Graph tags per page, a favicon (inline SVG data URL), and a 404.html.
5. Forms: label on every field, type and autocomplete attributes, required fields, messages next to the field, no accepting blank or nonsense input, a clear sending and thank-you state, and the form keeps what the visitor typed when there is an error. Forms post nowhere unless the user has an endpoint: show the thank-you state with JavaScript and say in your reply that a form service must be connected.
6. Pages are fast and light: no external fonts or libraries, images sized, nothing that blocks the first paint.
7. Run `ui_audit` and `ui_smoke` on every page until both are clean, then `view_output` the screenshots and look at the phone one.
8. Show the user how to open it: the path of index.html. Offer to zip it or to run it on a local port with bash_start.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
