---
name: build-code
description: Build a script, command line tool, library, REST API, backend or small service that has to work, with tests and run instructions. Use when the user asks for a CLI, a script, an API, a bot, a converter, a scraper or any program that is not a web page.
---

# Build code

Code is done when a stranger can run it, it survives bad input, and tests prove the parts that can break.

1. Write the feature list as your todo plan before coding: what the user asked, plus what every tool of this kind has. A CLI has: --help, a dry-run for anything that changes or deletes files, clear errors with exit code 2 for bad usage and 1 for failure, no traceback for expected problems, a summary at the end, and safe defaults (never overwrite or delete without --apply, --yes or a prompt). An API has: input validation with 4xx and a message, 404 for missing things, proper status codes, pagination, a health route, persistence that survives a restart, a documented example for each route. A script that reads files has: a missing file, an empty file, a wrong format, odd encodings (BOM, latin-1), names with spaces and non-ASCII, and a very large input.
2. Prefer the standard library, or one well known package listed in requirements.txt / package.json. Small modules with the logic apart from the input and output so it can be tested. No secrets in code; configuration through arguments or environment variables.
3. Tests: write them for the logic that can break, and for every edge case in your list (empty, bad, duplicate, boundary, collision, unicode). Use pytest, node:test or the language's standard runner. Run them with bash and fix until they pass. Test the real command or the real HTTP routes at least once end to end in a temporary folder or with the framework's test client. Do not weaken a test to make it pass.
4. Try it like the user: run the real command on realistic and on messy input (copy a sample into a scratch folder, never experiment on the user's own files without a dry-run first), run it twice to check it is repeatable, run it with no arguments, a wrong argument and --help. For a server, start it with bash_start, call each route with curl, restart it and check the data is still there, then stop it with bash_stop.
5. Write a README of at most 40 lines: what it does, how to install and run it (copy-paste commands), an example with its output, the options, how to run the tests. Make the file runnable (shebang, executable bit where it makes sense).
6. If the user also needs a screen for it (a simple web page for an API, say), build it with the build-ui skill and check it with `ui_audit` and `ui_smoke`.
7. Reply with how to run it, what you tested (commands and results in one line each) and what it deliberately does not do.

Rules: Write in plain sentences, no em dashes. Save results in the project folder (or "Meshpool outputs" if the user names none). Never delete or rename the user's own files while testing.
