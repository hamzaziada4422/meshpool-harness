import pytest

from app import create_app


@pytest.fixture
def client():
    return create_app().test_client()


def test_create_and_get(client):
    r = client.post("/notes", json={"title": "Groceries", "body": "milk", "tags": ["home"]})
    assert r.status_code == 201
    nid = r.get_json()["id"]
    assert client.get(f"/notes/{nid}").get_json()["title"] == "Groceries"


def test_title_required(client):
    assert client.post("/notes", json={"body": "x"}).status_code == 400


def test_missing_note(client):
    assert client.get("/notes/99").status_code == 404
