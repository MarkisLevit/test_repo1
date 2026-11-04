"""Бізнес-логіка складу. Спільна для API (Mini App) і Telegram-бота."""
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import ROLES, Item, Location, Movement, User


class NotFound(Exception):
    pass


class ValidationError(Exception):
    pass


@dataclass
class Actor:
    """Хто виконує дію (для журналу та перевірки прав)."""

    id: int
    name: str
    role: str

    @property
    def can_edit(self) -> bool:
        return self.role in ("editor", "admin")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def fmt_qty(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


# ---------------------------------------------------------------- users


def resolve_role(db: Session, user_id: int, admin_ids: frozenset[int]) -> str | None:
    if user_id in admin_ids:
        return "admin"
    user = db.get(User, user_id)
    return user.role if user else None


def upsert_user(db: Session, user_id: int, name: str = "", username: str = "", role: str | None = None) -> User:
    if role is not None and role not in ROLES:
        raise ValidationError(f"Невідома роль: {role}")
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id, name=name, username=username, role=role or "viewer")
        db.add(user)
    else:
        if name:
            user.name = name
        if username:
            user.username = username
        if role:
            user.role = role
    db.commit()
    return user


def remove_user(db: Session, user_id: int) -> bool:
    user = db.get(User, user_id)
    if not user:
        return False
    db.delete(user)
    db.commit()
    return True


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


# ---------------------------------------------------------------- locations


def _all_locations(db: Session) -> dict[int, Location]:
    return {loc.id: loc for loc in db.scalars(select(Location))}


def location_path(loc: Location | None, cache: dict[int, Location] | None = None) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    while loc is not None and loc.id not in seen:
        seen.add(loc.id)
        parts.append(loc.name)
        if loc.parent_id is None:
            break
        loc = cache.get(loc.parent_id) if cache is not None else loc.parent
    return " › ".join(reversed(parts))


def descendant_ids(db: Session, location_id: int) -> set[int]:
    locs = _all_locations(db)
    children: dict[int | None, list[int]] = {}
    for loc in locs.values():
        children.setdefault(loc.parent_id, []).append(loc.id)
    result, stack = set(), [location_id]
    while stack:
        cur = stack.pop()
        if cur in result:
            continue
        result.add(cur)
        stack.extend(children.get(cur, []))
    return result


def list_locations(db: Session) -> list[dict]:
    """Плоский список у порядку дерева, з глибиною, шляхом і кількістю речей."""
    locs = _all_locations(db)
    counts = dict(db.execute(select(Item.location_id, func.count(Item.id)).group_by(Item.location_id)).all())
    children: dict[int | None, list[Location]] = {}
    for loc in locs.values():
        parent = loc.parent_id if loc.parent_id in locs else None
        children.setdefault(parent, []).append(loc)
    for lst in children.values():
        lst.sort(key=lambda l: l.name.lower())

    out: list[dict] = []

    def walk(parent: int | None, depth: int, seen: set[int]) -> int:
        total = 0
        for loc in children.get(parent, []):
            if loc.id in seen:
                continue
            row = {
                "id": loc.id,
                "name": loc.name,
                "description": loc.description,
                "parent_id": loc.parent_id,
                "depth": depth,
                "path": location_path(loc, locs),
                "item_count": counts.get(loc.id, 0),
            }
            out.append(row)
            sub = walk(loc.id, depth + 1, seen | {loc.id})
            row["total_count"] = row["item_count"] + sub
            total += row["total_count"]
        return total

    walk(None, 0, set())
    return out


def get_location(db: Session, location_id: int) -> Location:
    loc = db.get(Location, location_id)
    if not loc:
        raise NotFound("Місце не знайдено")
    return loc


def _check_parent(db: Session, location_id: int | None, parent_id: int | None) -> None:
    if parent_id is None:
        return
    get_location(db, parent_id)
    if location_id is not None and parent_id in descendant_ids(db, location_id):
        raise ValidationError("Місце не може бути вкладене саме в себе")


def create_location(db: Session, name: str, parent_id: int | None = None, description: str = "") -> Location:
    name = name.strip()
    if not name:
        raise ValidationError("Вкажіть назву місця")
    _check_parent(db, None, parent_id)
    loc = Location(name=name, parent_id=parent_id, description=description.strip())
    db.add(loc)
    db.commit()
    return loc


def update_location(db: Session, location_id: int, name: str, parent_id: int | None, description: str = "") -> Location:
    loc = get_location(db, location_id)
    name = name.strip()
    if not name:
        raise ValidationError("Вкажіть назву місця")
    _check_parent(db, location_id, parent_id)
    loc.name, loc.parent_id, loc.description = name, parent_id, description.strip()
    db.commit()
    return loc


def delete_location(db: Session, location_id: int) -> None:
    """Речі та вкладені місця переходять до батьківського місця."""
    loc = get_location(db, location_id)
    # Через relationship, а не *_id: інакше SQLAlchemy при видаленні обнулить зв'язок.
    for item in list(loc.items):
        item.location = loc.parent
    for child in list(loc.children):
        child.parent = loc.parent
    db.flush()
    db.delete(loc)
    db.commit()


def find_location_by_name(db: Session, name: str) -> Location | None:
    needle = name.strip().lower()
    if not needle:
        return None
    for loc in db.scalars(select(Location)):
        if loc.name.lower() == needle:
            return loc
    return None


# ---------------------------------------------------------------- items


def item_to_dict(item: Item, locs: dict[int, Location] | None = None, with_history: bool = False) -> dict:
    loc = None
    if item.location_id is not None:
        loc = locs.get(item.location_id) if locs is not None else item.location
    data = {
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "sku": item.sku,
        "quantity": item.quantity,
        "unit": item.unit,
        "min_quantity": item.min_quantity,
        "is_low": item.is_low,
        "location_id": item.location_id,
        "location_path": location_path(loc, locs) if loc else "",
        "photo_url": f"/media/{item.photo}" if item.photo else None,
        "thumb_url": f"/media/thumb_{item.photo}" if item.photo else None,
        "updated_at": item.updated_at.isoformat() + "Z" if item.updated_at else None,
    }
    if with_history:
        data["history"] = [
            {
                "delta": m.delta,
                "quantity_after": m.quantity_after,
                "note": m.note,
                "user_name": m.user_name,
                "created_at": m.created_at.isoformat() + "Z",
            }
            for m in item.movements[:50]
        ]
    return data


def search_items(
    db: Session,
    q: str = "",
    location_id: int | None = None,
    low_only: bool = False,
    limit: int = 500,
) -> list[Item]:
    stmt = select(Item)
    for word in q.lower().split():
        stmt = stmt.where(Item.search_text.contains(word, autoescape=True))
    if location_id is not None:
        stmt = stmt.where(Item.location_id.in_(descendant_ids(db, location_id)))
    if low_only:
        stmt = stmt.where(Item.min_quantity > 0, Item.quantity <= Item.min_quantity)
    stmt = stmt.order_by(Item.name).limit(limit)
    return list(db.scalars(stmt))


def get_item(db: Session, item_id: int) -> Item:
    item = db.scalar(
        select(Item)
        .where(Item.id == item_id)
        .options(selectinload(Item.movements))
        .execution_options(populate_existing=True)  # свіжа історія після commit
    )
    if not item:
        raise NotFound("Річ не знайдено")
    return item


def _log(db: Session, item: Item, delta: float, note: str, actor: Actor | None) -> None:
    db.add(
        Movement(
            item=item,
            delta=delta,
            quantity_after=item.quantity,
            note=note,
            user_id=actor.id if actor else None,
            user_name=actor.name if actor else "",
        )
    )


def _validate_item_fields(fields: dict) -> None:
    if "name" in fields:
        fields["name"] = (fields["name"] or "").strip()
        if not fields["name"]:
            raise ValidationError("Вкажіть назву")
    for key in ("quantity", "min_quantity"):
        if key in fields and fields[key] is not None and fields[key] < 0:
            raise ValidationError("Кількість не може бути від'ємною")
    if "unit" in fields:
        fields["unit"] = (fields["unit"] or "шт").strip() or "шт"


def create_item(db: Session, actor: Actor | None, **fields) -> Item:
    _validate_item_fields(fields)
    if "name" not in fields:
        raise ValidationError("Вкажіть назву")
    if fields.get("location_id") is not None:
        get_location(db, fields["location_id"])
    item = Item(**fields)
    item.refresh_search_text()
    db.add(item)
    _log(db, item, item.quantity, "Створено", actor)
    db.commit()
    return item


def update_item(db: Session, item_id: int, actor: Actor | None, **fields) -> Item:
    item = get_item(db, item_id)
    _validate_item_fields(fields)
    notes: list[str] = []
    if "location_id" in fields and fields["location_id"] != item.location_id:
        locs = _all_locations(db)
        new_id = fields["location_id"]
        if new_id is not None and new_id not in locs:
            raise NotFound("Місце не знайдено")
        old = location_path(locs.get(item.location_id), locs) if item.location_id else "—"
        new = location_path(locs.get(new_id), locs) if new_id else "—"
        notes.append(f"Переміщено: {old} → {new}")
    delta = 0.0
    if "quantity" in fields and fields["quantity"] is not None and fields["quantity"] != item.quantity:
        delta = fields["quantity"] - item.quantity
    for key, value in fields.items():
        if value is not None or key == "location_id":
            setattr(item, key, value)
    item.refresh_search_text()
    if delta or notes:
        _log(db, item, delta, "; ".join(notes) or "Змінено кількість", actor)
    db.commit()
    return item


def adjust_quantity(db: Session, item_id: int, delta: float, actor: Actor | None, note: str = "") -> Item:
    item = get_item(db, item_id)
    if delta == 0:
        raise ValidationError("Зміна не може бути нульовою")
    if item.quantity + delta < 0:
        raise ValidationError(f"Недостатньо на складі: є {fmt_qty(item.quantity)} {item.unit}")
    item.quantity = item.quantity + delta
    _log(db, item, delta, note.strip() or ("Надходження" if delta > 0 else "Видача"), actor)
    db.commit()
    return item


def delete_item(db: Session, item_id: int, media_dir: Path) -> None:
    item = get_item(db, item_id)
    _remove_photo_files(media_dir, item.photo)
    db.delete(item)
    db.commit()


def stats(db: Session) -> dict:
    return {
        "items": db.scalar(select(func.count(Item.id))) or 0,
        "units": db.scalar(select(func.coalesce(func.sum(Item.quantity), 0))) or 0,
        "low": db.scalar(
            select(func.count(Item.id)).where(Item.min_quantity > 0, Item.quantity <= Item.min_quantity)
        )
        or 0,
        "locations": db.scalar(select(func.count(Location.id))) or 0,
    }


# ---------------------------------------------------------------- photos

MAX_PHOTO_SIDE = 1600
THUMB_SIDE = 400


def _remove_photo_files(media_dir: Path, name: str | None) -> None:
    if not name:
        return
    for fn in (name, f"thumb_{name}"):
        try:
            (media_dir / fn).unlink()
        except FileNotFoundError:
            pass


def save_photo(db: Session, item_id: int, raw: bytes, media_dir: Path) -> Item:
    """Зберігає фото (стискає до JPEG, робить мініатюру). Ім'я файлу — випадковий UUID."""
    item = get_item(db, item_id)
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as exc:  # Pillow кидає різні винятки на биті файли
        raise ValidationError("Файл не схожий на зображення") from exc

    media_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    full = img.copy()
    full.thumbnail((MAX_PHOTO_SIDE, MAX_PHOTO_SIDE))
    full.save(media_dir / name, "JPEG", quality=85, optimize=True)
    thumb = ImageOps.fit(img, (THUMB_SIDE, THUMB_SIDE))
    thumb.save(media_dir / f"thumb_{name}", "JPEG", quality=80, optimize=True)

    _remove_photo_files(media_dir, item.photo)
    item.photo = name
    db.commit()
    return item


def delete_photo(db: Session, item_id: int, media_dir: Path) -> Item:
    item = get_item(db, item_id)
    _remove_photo_files(media_dir, item.photo)
    item.photo = None
    db.commit()
    return item
