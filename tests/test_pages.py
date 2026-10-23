from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_table_page_renders():
    body = client.get("/").text
    assert "The table" in body
    assert 'href="/kev"' in body  # Kev's own Plate in the nav (Access off = Kev)


def test_plate_page_names_its_owner():
    assert "<h1>Kev's plate</h1>" in client.get("/kev").text


def test_unknown_space_is_404():
    assert client.get("/nobody").status_code == 404
