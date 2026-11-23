class NoteStore:
    def __init__(self):
        self._notes = {}
        self._next = 1

    def all(self):
        return [self._notes[k] for k in sorted(self._notes)]

    def add(self, title, body="", tags=()):
        note = {"id": self._next, "title": title, "body": body, "tags": list(tags)}
        self._notes[self._next] = note
        self._next += 1
        return note

    def get(self, note_id):
        return self._notes.get(note_id)
