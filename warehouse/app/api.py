"""HTTP API для Mini App."""
from __future__ import annotations

import io
from typing import Annotated, Iterator

import qrcode
from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import services as svc
from .auth import InitDataError, validate_init_data
from .config import Settings

router = APIRouter(prefix="/api")

MAX_UPLOAD = 15 * 1024 * 1024


# ---------------------------------------------------------------- dependencies


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]


def get_actor(
    db: DB,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> svc.Actor:
    init_data = ""
    if authorization and authorization.lower().startswith("tma "):
        init_data = authorization[4:]

    if not init_data:
        if settings.dev_mode:
            dev_id = min(settings.admin_ids) if settings.admin_ids else 0
            return svc.Actor(id=dev_id, name="Dev", role="admin")
        raise HTTPException(401, "Відкрийте застосунок через Telegram")

    try:
        data = validate_init_data(init_data, settings.bot_token, settings.init_data_ttl)
    except InitDataError as exc:
        raise HTTPException(401, str(exc)) from exc

    user = data.get("user") or {}
    user_id = int(user.get("id", 0))
    role = svc.resolve_role(db, user_id, settings.admin_ids)
    if role is None:
        raise HTTPException(403, f"Немає доступу. Попросіть адміністратора додати ваш ID: {user_id}")
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or user.get("username", "")
    return svc.Actor(id=user_id, name=name or str(user_id), role=role)


ActorDep = Annotated[svc.Actor, Depends(get_actor)]


def editor(actor: ActorDep) -> svc.Actor:
    if not actor.can_edit:
        raise HTTPException(403, "Тільки перегляд: у вас немає прав на зміни")
    return actor


EditorDep = Annotated[svc.Actor, Depends(editor)]


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except svc.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except svc.ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------- schemas


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str = ""
    sku: str = Field("", max_length=100)
    quantity: float = Field(0, ge=0)
    unit: str = Field("шт", max_length=20)
    min_quantity: float = Field(0, ge=0)
    location_id: int | None = None


class ItemPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = None
    sku: str | None = Field(None, max_length=100)
    quantity: float | None = Field(None, ge=0)
    unit: str | None = Field(None, max_length=20)
    min_quantity: float | None = Field(None, ge=0)
    location_id: int | None = None


class AdjustIn(BaseModel):
    delta: float
    note: str = ""


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    parent_id: int | None = None


# ---------------------------------------------------------------- routes


@router.get("/me")
def me(actor: ActorDep, request: Request):
    return {
        "id": actor.id,
        "name": actor.name,
        "role": actor.role,
        "can_edit": actor.can_edit,
        "app_link": request.app.state.app_link,
    }


@router.get("/stats")
def get_stats(db: DB, _: ActorDep):
    return svc.stats(db)


@router.get("/items")
def list_items(db: DB, _: ActorDep, q: str = "", location_id: int | None = None, low: bool = False):
    locs = svc._all_locations(db)
    return [svc.item_to_dict(i, locs) for i in svc.search_items(db, q, location_id, low)]


@router.post("/items", status_code=201)
def create_item(body: ItemIn, db: DB, actor: EditorDep):
    item = _handle(svc.create_item, db, actor, **body.model_dump())
    return svc.item_to_dict(item)


@router.get("/items/{item_id}")
def read_item(item_id: int, db: DB, _: ActorDep):
    item = _handle(svc.get_item, db, item_id)
    return svc.item_to_dict(item, svc._all_locations(db), with_history=True)


@router.patch("/items/{item_id}")
def patch_item(item_id: int, body: ItemPatch, db: DB, actor: EditorDep):
    item = _handle(svc.update_item, db, item_id, actor, **body.model_dump(exclude_unset=True))
    return svc.item_to_dict(svc.get_item(db, item.id), svc._all_locations(db), with_history=True)


@router.post("/items/{item_id}/adjust")
def adjust(item_id: int, body: AdjustIn, db: DB, actor: EditorDep):
    _handle(svc.adjust_quantity, db, item_id, body.delta, actor, body.note)
    return svc.item_to_dict(svc.get_item(db, item_id), svc._all_locations(db), with_history=True)


@router.delete("/items/{item_id}", status_code=204)
def remove_item(item_id: int, db: DB, _: EditorDep, settings: Annotated[Settings, Depends(get_settings)]):
    _handle(svc.delete_item, db, item_id, settings.media_dir)
    return Response(status_code=204)


@router.post("/items/{item_id}/photo")
async def upload_photo(
    item_id: int,
    db: DB,
    _: EditorDep,
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(...),
):
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Фото завелике (максимум 15 МБ)")
    item = _handle(svc.save_photo, db, item_id, raw, settings.media_dir)
    return svc.item_to_dict(item)


@router.delete("/items/{item_id}/photo")
def remove_photo(item_id: int, db: DB, _: EditorDep, settings: Annotated[Settings, Depends(get_settings)]):
    item = _handle(svc.delete_photo, db, item_id, settings.media_dir)
    return svc.item_to_dict(item)


@router.get("/locations")
def locations(db: DB, _: ActorDep):
    return svc.list_locations(db)


@router.post("/locations", status_code=201)
def create_location(body: LocationIn, db: DB, _: EditorDep):
    loc = _handle(svc.create_location, db, body.name, body.parent_id, body.description)
    return {"id": loc.id, "name": loc.name, "parent_id": loc.parent_id}


@router.put("/locations/{location_id}")
def update_location(location_id: int, body: LocationIn, db: DB, _: EditorDep):
    loc = _handle(svc.update_location, db, location_id, body.name, body.parent_id, body.description)
    return {"id": loc.id, "name": loc.name, "parent_id": loc.parent_id}


@router.delete("/locations/{location_id}", status_code=204)
def delete_location(location_id: int, db: DB, _: EditorDep):
    _handle(svc.delete_location, db, location_id)
    return Response(status_code=204)


@router.get("/qr/{kind}/{obj_id}.png")
def qr_png(kind: str, obj_id: int, request: Request):
    """QR-код з посиланням на річ/місце. Містить лише посилання, тому без авторизації
    (тег <img> не вміє передавати заголовок Authorization)."""
    if kind not in ("item", "loc"):
        raise HTTPException(404)
    link = f"{request.app.state.app_link}{kind}_{obj_id}"
    img = qrcode.make(link, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
