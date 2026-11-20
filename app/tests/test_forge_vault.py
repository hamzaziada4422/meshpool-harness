"""The layout vault (engines/forge/vault.py): public notes for Forge's own layouts (committed, and kept in step with
the generator) and private notes for library items (written into MESHPOOL_KB only)."""
import json

from engines.forge import vault as V


def test_public_vault_is_current_and_linked(tmp_path):
    n = V.build_public(tmp_path)
    assert n == len(V.ARCHETYPES)
    for p in tmp_path.glob("*.md"):                         # the committed notes are what the generator writes
        assert (V.PUBLIC / p.name).read_text() == p.read_text(), f"run: python -m engines.forge.vault public ({p.name})"
    note = (tmp_path / "risk_matrix.md").read_text()
    assert note.startswith("---\ntype: \"forge-archetype\"") and "[[matrix_3x3]]" in note and "[[diagram]]" in note
    for name, v in V.ARCHETYPES.items():                    # every link points at a note
        assert all(r in V.ARCHETYPES for r in v[4]), name


def test_search_ranks_by_the_message_and_skips_maps():
    hits = V.search("these risks matter most", private=False)
    assert hits[0]["name"] == "risk_matrix" and all(h["name"] not in V.FAMILIES for h in hits)
    assert V.search("of the and", private=False) == []


def test_private_vault_from_layout_labels(tmp_path, monkeypatch):
    import sqlite3
    from test_forge_kb import _deck, _thumb
    r = tmp_path / "kb"
    (r / "library" / "sourced").mkdir(parents=True)
    (r / "thumbs").mkdir()
    con = sqlite3.connect(r / "library" / "components.db")
    con.execute("CREATE TABLE items(id TEXT PRIMARY KEY, grp TEXT, counted INT, scope TEXT, category TEXT, subcategory "
                "TEXT, source TEXT, source_file TEXT, slide_no INT, title TEXT, text_sample TEXT, n_nodes INT, presets "
                "TEXT, colors TEXT, fonts TEXT, w_in REAL, h_in REAL, area_pct REAL, slide_w INT, slide_h INT, hash "
                "TEXT, file TEXT, thumb TEXT, bytes INT, tier TEXT)")
    con.execute("CREATE VIRTUAL TABLE items_fts USING fts5(id UNINDEXED, category, subcategory, title, text_sample, "
                "content='')")
    for iid in ("a1", "a2"):
        _deck(r / "library" / "sourced" / f"{iid}.pptx", ["Step one", "Step two"])
        _thumb(r / "thumbs" / f"{iid}.webp", "steps")
        con.execute("INSERT INTO items (id, grp, scope, category, file, thumb, tier, w_in, h_in) VALUES "
                    "(?,?,?,?,?,?,?,?,?)", (iid, "sourced", "full_slide", "process_flow",
                                            f"library/sourced/{iid}.pptx", f"thumbs/{iid}.webp", "business", 12, 6))
    con.commit()
    con.close()
    monkeypatch.setenv("MESHPOOL_KB", str(r))
    recs = [{"id": i, "name": f"Four chevron steps {i}", "use_when": "show how a process runs end to end",
             "structure": "four chevrons with captions", "slots": [{"k": 0, "role": "label", "holds": "step name",
                                                                    "max_words": 3}],
             "forge_archetype": "grid", "density": "light"} for i in ("a1", "a2")]
    assert V.store_layouts(recs) == 2
    assert V.build_private() == 2
    notes = sorted((r / "vault" / "items").glob("*.md"))
    assert len(notes) == 2
    t = notes[0].read_text()
    assert "type: \"library-item\"" in t and "[[grid]]" in t and "[[process_flow]]" in t and "Similar: [[" in t
    assert (r / "vault" / "process_flow.md").exists() and (r / "vault" / "index.md").exists()
    hits = V.search("how a process runs end to end")
    assert any(h["vault"] == "private" for h in hits)
    json.dumps(hits)
