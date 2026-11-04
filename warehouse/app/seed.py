"""Заповнює порожню базу демо-даними:  python -m app.seed"""
from __future__ import annotations

from .config import Settings
from .models import make_session_factory
from . import services as svc


def main() -> None:
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    Session = make_session_factory(f"sqlite:///{settings.db_path}")
    with Session() as db:
        if svc.stats(db)["items"]:
            print("База не порожня — пропускаю.")
            return
        actor = svc.Actor(id=0, name="Демо", role="admin")
        wh = svc.create_location(db, "Основний склад")
        a = svc.create_location(db, "Стелаж А", wh.id)
        a1 = svc.create_location(db, "Полиця 1", a.id)
        a2 = svc.create_location(db, "Полиця 2", a.id)
        b = svc.create_location(db, "Стелаж Б", wh.id)
        office = svc.create_location(db, "Офіс", description="Шафа біля входу")
        rows = [
            ("Дриль Bosch GSB 13 RE", "TL-001", 3, "шт", 1, a1.id, "Ударна, 600 Вт, у кейсі"),
            ("Шуруповерт Makita", "TL-002", 2, "шт", 1, a1.id, ""),
            ("Саморізи 4×40", "FX-040", 1.5, "кг", 2, a2.id, "По дереву, жовтий цинк"),
            ("Подовжувач 10 м", "EL-010", 4, "шт", 0, b.id, ""),
            ("Стрічка малярна", "CN-005", 12, "шт", 5, b.id, ""),
            ("Ноутбук Lenovo T14", "IT-1001", 1, "шт", 0, office.id, "Інв. № 1001, видається під підпис"),
            ("Папір А4", "OF-A4", 0, "уп", 3, office.id, ""),
        ]
        for name, sku, qty, unit, mn, loc, desc in rows:
            svc.create_item(
                db, actor, name=name, sku=sku, quantity=qty, unit=unit, min_quantity=mn, location_id=loc, description=desc
            )
        print("Демо-дані додано.")


if __name__ == "__main__":
    main()
