import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.auth import InitDataError, sign_init_data, validate_init_data
from app.config import Settings
from app.main import create_app

TOKEN = "123456:TEST-token"
ADMIN = 1001
EDITOR = 2002
VIEWER = 3003


def init_data(user_id: int, token: str = TOKEN, auth_date: int | None = None, **extra) -> str:
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAH",
        "user": json.dumps({"id": user_id, "first_name": "Тест", "username": f"u{user_id}"}),
        **extra,
    }
    return sign_init_data(fields, token)


def hdr(user_id: int) -> dict:
    return {"Authorization": "tma " + init_data(user_id)}


@pytest.fixture
def client(tmp_path):
    settings = Settings(bot_token=TOKEN, admin_ids=frozenset({ADMIN}), data_dir=tmp_path, run_bot=False)
    app = create_app(settings)
    with TestClient(app) as c:
        with app.state.session_factory() as db:
            from app.services import upsert_user

            upsert_user(db, EDITOR, role="editor")
            upsert_user(db, VIEWER, role="viewer")
        yield c


# ---------------------------------------------------------------- auth


def test_valid_init_data():
    data = validate_init_data(init_data(42), TOKEN)
    assert data["user"]["id"] == 42


def test_init_data_with_signature_field():
    data = validate_init_data(init_data(42, signature="abc"), TOKEN)
    assert data["user"]["id"] == 42


@pytest.mark.parametrize(
    "raw",
    [
        "",
        init_data(42, token="999:other"),
        init_data(42).replace("u42", "u43"),
        init_data(42, auth_date=int(time.time()) - 3 * 86400),
        "auth_date=1&user=%7B%7D",
    ],
)
def test_invalid_init_data(raw):
    with pytest.raises(InitDataError):
        validate_init_data(raw, TOKEN)


def test_api_requires_auth(client):
    assert client.get("/api/me").status_code == 401
    r = client.get("/api/me", headers=hdr(5555))
    assert r.status_code == 403
    assert "5555" in r.json()["detail"]


def test_roles(client):
    assert client.get("/api/me", headers=hdr(ADMIN)).json()["role"] == "admin"
    assert client.get("/api/me", headers=hdr(EDITOR)).json()["can_edit"] is True
    me = client.get("/api/me", headers=hdr(VIEWER)).json()
    assert me["can_edit"] is False
    assert client.get("/api/items", headers=hdr(VIEWER)).status_code == 200
    assert client.post("/api/items", json={"name": "x"}, headers=hdr(VIEWER)).status_code == 403


def test_dev_mode_without_telegram(tmp_path):
    settings = Settings(admin_ids=frozenset({ADMIN}), data_dir=tmp_path, run_bot=False, dev_mode=True)
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/me").json()["role"] == "admin"


# ---------------------------------------------------------------- warehouse flow


def test_full_flow(client):
    h = hdr(EDITOR)
    wh = client.post("/api/locations", json={"name": "Склад А"}, headers=h).json()
    rack = client.post("/api/locations", json={"name": "Стелаж 1", "parent_id": wh["id"]}, headers=h).json()
    shelf = client.post("/api/locations", json={"name": "Полиця 2", "parent_id": rack["id"]}, headers=h).json()

    locs = client.get("/api/locations", headers=h).json()
    assert [l["depth"] for l in locs] == [0, 1, 2]
    assert locs[2]["path"] == "Склад А › Стелаж 1 › Полиця 2"

    r = client.post(
        "/api/items",
        json={"name": "Дриль Bosch", "sku": "TL-001", "quantity": 3, "min_quantity": 2, "location_id": shelf["id"]},
        headers=h,
    )
    assert r.status_code == 201
    drill = r.json()
    assert drill["location_path"] == "Склад А › Стелаж 1 › Полиця 2"
    client.post("/api/items", json={"name": "Шуруповерт", "quantity": 10}, headers=h)

    # Пошук без урахування регістру — і для кирилиці
    assert [i["name"] for i in client.get("/api/items?q=ДРИЛЬ", headers=h).json()] == ["Дриль Bosch"]
    assert [i["name"] for i in client.get("/api/items?q=tl-001", headers=h).json()] == ["Дриль Bosch"]
    # Фільтр за місцем включає вкладені місця
    assert len(client.get(f"/api/items?location_id={wh['id']}", headers=h).json()) == 1

    # Видача / надходження
    r = client.post(f"/api/items/{drill['id']}/adjust", json={"delta": -1, "note": "Іванову"}, headers=h)
    assert r.json()["quantity"] == 2 and r.json()["is_low"] is True
    assert client.get("/api/items?low=1", headers=h).json()[0]["id"] == drill["id"]
    r = client.post(f"/api/items/{drill['id']}/adjust", json={"delta": -5}, headers=h)
    assert r.status_code == 400

    # Переміщення пишеться в історію
    r = client.patch(f"/api/items/{drill['id']}", json={"location_id": wh["id"]}, headers=h)
    item = r.json()
    assert item["location_path"] == "Склад А"
    notes = [m["note"] for m in item["history"]]
    assert notes[0].startswith("Переміщено") and "Іванову" in notes and "Створено" in notes
    assert item["history"][0]["user_name"] == "Тест"

    stats = client.get("/api/stats", headers=h).json()
    assert stats == {"items": 2, "units": 12, "low": 1, "locations": 3}

    # Не можна вкласти місце саме в себе
    r = client.put(f"/api/locations/{wh['id']}", json={"name": "Склад А", "parent_id": shelf["id"]}, headers=h)
    assert r.status_code == 400

    # Видалення місця: речі піднімаються на рівень вище
    client.patch(f"/api/items/{drill['id']}", json={"location_id": shelf["id"]}, headers=h)
    assert client.delete(f"/api/locations/{shelf['id']}", headers=h).status_code == 204
    assert client.get(f"/api/items/{drill['id']}", headers=h).json()["location_id"] == rack["id"]

    assert client.delete(f"/api/items/{drill['id']}", headers=h).status_code == 204
    assert client.get(f"/api/items/{drill['id']}", headers=h).status_code == 404


def test_photo_upload(client, tmp_path):
    h = hdr(ADMIN)
    item = client.post("/api/items", json={"name": "Коробка"}, headers=h).json()
    buf = io.BytesIO()
    Image.new("RGB", (2000, 1000), "orange").save(buf, "PNG")
    r = client.post(f"/api/items/{item['id']}/photo", files={"file": ("a.png", buf.getvalue(), "image/png")}, headers=h)
    assert r.status_code == 200, r.text
    url = r.json()["photo_url"]
    img = Image.open(io.BytesIO(client.get(url).content))
    assert max(img.size) == 1600
    assert client.get(r.json()["thumb_url"]).status_code == 200

    bad = client.post(f"/api/items/{item['id']}/photo", files={"file": ("a.png", b"nope", "image/png")}, headers=h)
    assert bad.status_code == 400

    client.delete(f"/api/items/{item['id']}", headers=h)
    assert list((tmp_path / "media").iterdir()) == []


def test_qr_and_static(client):
    r = client.get("/api/qr/item/5.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client.get("/api/qr/bad/5.png").status_code == 404
    assert "Склад" in client.get("/").text
