# notes-api

A tiny Flask API for notes. Start it with `python app.py`; run tests with `python -m pytest -q`.

Endpoints today:
- `GET /notes` -> list of notes `[{"id", "title", "body", "tags"}]`
- `POST /notes` with JSON `{"title", "body", "tags"?}` -> 201 and the created note; 400 if title is missing
- `GET /notes/<id>` -> the note, or 404
