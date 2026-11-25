from flask import Flask, jsonify, request

from store import NoteStore


def create_app(store=None):
    app = Flask(__name__)
    app.config["store"] = store or NoteStore()

    @app.get("/notes")
    def list_notes():
        return jsonify(app.config["store"].all())

    @app.post("/notes")
    def add_note():
        data = request.get_json(silent=True) or {}
        if not str(data.get("title") or "").strip():
            return jsonify({"error": "title is required"}), 400
        note = app.config["store"].add(data["title"], data.get("body", ""), data.get("tags") or [])
        return jsonify(note), 201

    @app.get("/notes/<int:note_id>")
    def get_note(note_id):
        note = app.config["store"].get(note_id)
        if note is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(note)

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
