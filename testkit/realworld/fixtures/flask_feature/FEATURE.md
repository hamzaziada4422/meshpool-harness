# Feature request: search, edit and delete notes

1. `GET /notes?tag=<tag>` returns only the notes that have that tag (exact, case-insensitive match). Without the
   parameter, all notes are returned as today.
2. `GET /notes?q=<text>` returns only notes whose title or body contains the text (case-insensitive). `tag` and
   `q` can be combined (both must match).
3. `PATCH /notes/<id>` with a JSON object updates any of `title`, `body`, `tags` and returns 200 with the
   updated note. 404 if the note does not exist. 400 if `title` is present but empty, or if `tags` is present
   but not a list of strings. Other fields in the request are ignored.
4. `DELETE /notes/<id>` returns 204 with an empty body; 404 if the note does not exist. Deleted ids are never
   reused.
