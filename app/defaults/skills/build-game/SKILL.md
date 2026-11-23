---
name: build-game
description: Build a playable game in one HTML file (canvas or DOM) and play-test it in the browser. Use when the user asks for a game, a quiz, a puzzle or a toy.
---

# Build game

A game is done when you have played it to the game over screen and back.

1. Write the feature list as your todo plan. Every game needs: a start screen with the controls shown, play, score, difficulty that rises, pause (P or Space), game over with the final score, a best score kept in localStorage, restart without reloading, and sound that can be muted (or none).
2. One HTML file with inline JavaScript, no libraries unless asked. Copy the look from the build-ui skill's starter.html. Use requestAnimationFrame with a fixed time step so speed does not depend on the screen. Pause when the tab is hidden.
3. Controls: arrow keys and WASD both work, and touch works on a phone (swipe or on-screen buttons). Call preventDefault on game keys so the page does not scroll. Cannot reverse into yourself, cannot press two keys in one tick and lose, no input lost between ticks. The canvas scales to the screen width and stays sharp (use devicePixelRatio).
4. Rules are correct at the edges: walls, self-hit, the last free cell, a score that adds exactly once, restart that resets every variable.
5. Play-test it, do not just look: with a Playwright script or the browser tools press Start, steer, collect points, check the score changes, lose on purpose, see game over and the best score, restart and play again, pause and resume, then do the same with touch at 390 px. Run `ui_audit` and `ui_smoke` and fix every item.
6. Reply with the file path, the controls in one line, and what you play-tested.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done.
