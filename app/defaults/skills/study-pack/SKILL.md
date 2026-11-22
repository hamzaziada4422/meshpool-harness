---
name: study-pack
description: Turn notes, a chapter or a lecture into flashcards, a practice quiz and a summary. Use when a student asks for flashcards, a quiz, a study guide or exam prep.
---

# Study pack

1. Read the material. List the 15 to 40 facts, terms and ideas worth knowing; skip trivia.
2. Flashcards: a CSV with Front and Back columns (imports into Anki and Quizlet), one fact per card, questions that need recall, not yes or no.
3. Practice quiz: a single HTML file (read the build-game skill) with multiple choice, immediate feedback, a score and a retry button. Answers are checked in JavaScript from data in the file.
4. A one-page study guide with make_document: the main ideas, then the hardest five cards with the explanation.
5. Check the quiz in the browser and with ui_audit. Never invent facts that are not in the material.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output before you say it is done. Reply with the file path and two sentences on what it is.
