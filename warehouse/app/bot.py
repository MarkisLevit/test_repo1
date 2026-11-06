"""Telegram-бот: відкриває Mini App, шукає речі, швидко додає річ з фото."""
from __future__ import annotations

import asyncio
import html
import io
import logging
from typing import Callable

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from sqlalchemy.orm import Session, sessionmaker

from . import services as svc
from .config import Settings

log = logging.getLogger(__name__)

ROLE_NAMES = {"viewer": "перегляд", "editor": "редагування", "admin": "адміністратор"}

HELP = (
    "<b>Склад</b> — облік речей: що є, скільки і де лежить.\n\n"
    "🔎 Напишіть назву або артикул — я знайду річ.\n"
    "📷 Надішліть фото з підписом — додам нову річ.\n"
    "   Формат підпису: <code>Назва | кількість | місце</code>\n"
    "   (кількість і місце необов'язкові)\n\n"
    "/app — відкрити застосунок\n"
    "/low — що закінчується\n"
    "/stats — коротка статистика\n"
    "/myid — ваш Telegram ID\n\n"
    "<b>Для адміністратора</b>\n"
    "/users — список користувачів\n"
    "/allow &lt;id&gt; [viewer|editor|admin] — дати доступ\n"
    "/deny &lt;id&gt; — забрати доступ"
)


def build_dispatcher(settings: Settings, session_factory: sessionmaker) -> Dispatcher:
    router = Router()
    https = settings.webapp_url.startswith("https://")

    async def run_db(fn: Callable[[Session], object]):
        """Синхронна робота з SQLite в окремому потоці, щоб не блокувати бота."""

        def _call():
            with session_factory() as db:
                return fn(db)

        return await asyncio.to_thread(_call)

    async def actor_for(message_or_cb) -> svc.Actor | None:
        user = message_or_cb.from_user
        role = await run_db(lambda db: svc.resolve_role(db, user.id, settings.admin_ids))
        if role is None:
            return None
        return svc.Actor(id=user.id, name=user.full_name, role=role)

    def app_button(text: str = "📦 Відкрити склад", query: str = "") -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=f"{settings.webapp_url}/{query}"))

    def app_keyboard(query: str = "", text: str = "📦 Відкрити склад") -> InlineKeyboardMarkup | None:
        if not https:
            return None
        return InlineKeyboardMarkup(inline_keyboard=[[app_button(text, query)]])

    async def deny_access(message: Message) -> None:
        user = message.from_user
        await message.answer(
            "⛔ У вас поки немає доступу до складу.\n"
            f"Ваш ID: <code>{user.id}</code>\n"
            "Я надіслав запит адміністратору."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="👁 Перегляд", callback_data=f"grant:{user.id}:viewer"),
                    InlineKeyboardButton(text="✏️ Редагування", callback_data=f"grant:{user.id}:editor"),
                ]
            ]
        )
        uname = f" @{user.username}" if user.username else ""
        for admin_id in settings.admin_ids:
            try:
                await message.bot.send_message(
                    admin_id,
                    f"🔔 Запит доступу: {html.escape(user.full_name)}{uname} (ID <code>{user.id}</code>)",
                    reply_markup=kb,
                )
            except Exception:  # адмін міг не запускати бота
                log.warning("Не вдалося повідомити адміна %s", admin_id)

    def item_line(d: dict) -> str:
        qty = f"{svc.fmt_qty(d['quantity'])} {html.escape(d['unit'])}"
        warn = " ⚠️" if d["is_low"] else ""
        loc = html.escape(d["location_path"]) or "місце не вказано"
        sku = f" · <code>{html.escape(d['sku'])}</code>" if d["sku"] else ""
        return f"<b>{html.escape(d['name'])}</b>{sku}\n   {qty}{warn} · 📍 {loc}"

    # ------------------------------------------------------------ handlers

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject):
        actor = await actor_for(message)
        if actor is None:
            await deny_access(message)
            return
        query = f"?startapp={command.args}" if command.args else ""
        await message.answer(
            f"Привіт, {html.escape(message.from_user.first_name)}! "
            f"Ваш рівень доступу: <b>{ROLE_NAMES[actor.role]}</b>.\n\n" + HELP,
            reply_markup=app_keyboard(query),
        )

    @router.message(Command("help"))
    async def help_cmd(message: Message):
        await message.answer(HELP, reply_markup=app_keyboard())

    @router.message(Command("myid"))
    async def myid(message: Message):
        await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")

    @router.message(Command("app"))
    async def app_cmd(message: Message):
        if await actor_for(message) is None:
            await deny_access(message)
            return
        if not https:
            await message.answer("WEBAPP_URL має бути HTTPS-адресою, щоб відкрити Mini App.")
            return
        await message.answer("Відкрийте склад:", reply_markup=app_keyboard())

    @router.message(Command("stats"))
    async def stats_cmd(message: Message):
        if await actor_for(message) is None:
            await deny_access(message)
            return
        s = await run_db(svc.stats)
        await message.answer(
            f"📦 Позицій: <b>{s['items']}</b>\n"
            f"🔢 Одиниць усього: <b>{svc.fmt_qty(s['units'])}</b>\n"
            f"📍 Місць зберігання: <b>{s['locations']}</b>\n"
            f"⚠️ Закінчується: <b>{s['low']}</b>"
        )

    @router.message(Command("low"))
    async def low_cmd(message: Message):
        if await actor_for(message) is None:
            await deny_access(message)
            return

        def q(db):
            locs = svc._all_locations(db)
            return [svc.item_to_dict(i, locs) for i in svc.search_items(db, low_only=True, limit=30)]

        items = await run_db(q)
        if not items:
            await message.answer("✅ Нічого не закінчується.")
            return
        text = "⚠️ <b>Закінчується:</b>\n\n" + "\n\n".join(item_line(d) for d in items)
        await message.answer(text, reply_markup=app_keyboard("?low=1", "Відкрити список"))

    # ---------------- admin

    async def require_admin(message: Message) -> bool:
        actor = await actor_for(message)
        if actor is None or not actor.is_admin:
            await message.answer("Ця команда лише для адміністратора.")
            return False
        return True

    @router.message(Command("users"))
    async def users_cmd(message: Message):
        if not await require_admin(message):
            return
        users = await run_db(svc.list_users)
        lines = [f"👑 <code>{i}</code> — адміністратор (з ADMIN_IDS)" for i in sorted(settings.admin_ids)]
        for u in users:
            if u.id in settings.admin_ids:
                continue
            uname = f" @{u.username}" if u.username else ""
            lines.append(
                f"• <code>{u.id}</code> {html.escape(u.name)}{uname} — {ROLE_NAMES.get(u.role, u.role)}"
            )
        await message.answer("<b>Користувачі</b>\n" + "\n".join(lines))

    @router.message(Command("allow"))
    async def allow_cmd(message: Message, command: CommandObject):
        if not await require_admin(message):
            return
        parts = (command.args or "").split()
        if not parts or not parts[0].lstrip("-").isdigit():
            await message.answer("Формат: /allow &lt;id&gt; [viewer|editor|admin]")
            return
        role = parts[1] if len(parts) > 1 else "viewer"
        if role not in ROLE_NAMES:
            await message.answer("Роль має бути: viewer, editor або admin")
            return
        uid = int(parts[0])
        await run_db(lambda db: svc.upsert_user(db, uid, role=role))
        await message.answer(f"✅ Користувач <code>{uid}</code> отримав доступ: {ROLE_NAMES[role]}")

    @router.message(Command("deny"))
    async def deny_cmd(message: Message, command: CommandObject):
        if not await require_admin(message):
            return
        arg = (command.args or "").strip()
        if not arg.lstrip("-").isdigit():
            await message.answer("Формат: /deny &lt;id&gt;")
            return
        removed = await run_db(lambda db: svc.remove_user(db, int(arg)))
        await message.answer("🗑 Доступ забрано." if removed else "Такого користувача немає.")

    @router.callback_query(F.data.startswith("grant:"))
    async def grant_cb(cb: CallbackQuery):
        actor = await actor_for(cb)
        if actor is None or not actor.is_admin:
            await cb.answer("Лише для адміністратора", show_alert=True)
            return
        _, uid, role = cb.data.split(":")
        await run_db(lambda db: svc.upsert_user(db, int(uid), role=role))
        await cb.message.edit_text(cb.message.html_text + f"\n\n✅ Надано: {ROLE_NAMES[role]}")
        await cb.answer("Готово")
        try:
            await cb.bot.send_message(
                int(uid),
                f"✅ Вам надано доступ до складу ({ROLE_NAMES[role]}). Натисніть /start",
            )
        except Exception:
            pass

    # ---------------- швидке додавання з фото

    @router.message(F.photo)
    async def photo_add(message: Message):
        actor = await actor_for(message)
        if actor is None:
            await deny_access(message)
            return
        if not actor.can_edit:
            await message.answer("У вас доступ лише на перегляд.")
            return
        caption = (message.caption or "").strip()
        if not caption:
            await message.answer(
                "Додайте підпис до фото: <code>Назва | кількість | місце</code>"
            )
            return
        parts = [p.strip() for p in caption.replace(";", "|").split("|")]
        name = parts[0]
        qty = 1.0
        if len(parts) > 1 and parts[1]:
            try:
                qty = float(parts[1].replace(",", "."))
            except ValueError:
                await message.answer("Кількість має бути числом, напр.: <code>Дриль | 2 | Стелаж А</code>")
                return
        loc_name = parts[2] if len(parts) > 2 else ""

        buf = io.BytesIO()
        await message.bot.download(message.photo[-1], destination=buf)

        def create(db):
            loc = svc.find_location_by_name(db, loc_name) if loc_name else None
            item = svc.create_item(db, actor, name=name, quantity=qty, location_id=loc.id if loc else None)
            svc.save_photo(db, item.id, buf.getvalue(), settings.media_dir)
            return svc.item_to_dict(svc.get_item(db, item.id), svc._all_locations(db)), loc is not None

        try:
            data, loc_found = await run_db(create)
        except (svc.ValidationError, svc.NotFound) as exc:
            await message.answer(f"❌ {html.escape(str(exc))}")
            return
        note = ""
        if loc_name and not loc_found:
            note = f"\n\n⚠️ Місце «{html.escape(loc_name)}» не знайдено — вкажіть його в застосунку."
        await message.answer(
            "✅ Додано:\n" + item_line(data) + note,
            reply_markup=app_keyboard(f"?startapp=item_{data['id']}", "Відкрити картку"),
        )

    # ---------------- пошук будь-яким текстом

    @router.message(F.text & ~F.text.startswith("/"))
    async def search(message: Message):
        if await actor_for(message) is None:
            await deny_access(message)
            return
        query = message.text.strip()

        def q(db):
            locs = svc._all_locations(db)
            items = svc.search_items(db, query, limit=11)
            return [(svc.item_to_dict(i, locs), i.photo) for i in items]

        found = await run_db(q)
        if not found:
            await message.answer(f"🤷 Нічого не знайдено за «{html.escape(query)}».")
            return
        if len(found) == 1:
            d, photo = found[0]
            kb = app_keyboard(f"?startapp=item_{d['id']}", "Відкрити картку")
            if photo and (settings.media_dir / photo).exists():
                img = BufferedInputFile((settings.media_dir / photo).read_bytes(), filename=photo)
                await message.answer_photo(img, caption=item_line(d), reply_markup=kb)
            else:
                await message.answer(item_line(d), reply_markup=kb)
            return
        more = "\n\n…і ще. Уточніть запит або відкрийте застосунок." if len(found) > 10 else ""
        text = f"🔎 Знайдено за «{html.escape(query)}»:\n\n" + "\n\n".join(item_line(d) for d, _ in found[:10])
        rows = []
        if https:
            rows = [[app_button(f"{d['name'][:30]}", f"?startapp=item_{d['id']}")] for d, _ in found[:5]]
        await message.answer(text + more, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)

    dp = Dispatcher()
    dp.include_router(router)
    return dp


def build_bot(settings: Settings) -> Bot:
    return Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def setup_bot_ui(bot: Bot, settings: Settings) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="app", description="Відкрити склад"),
            BotCommand(command="low", description="Що закінчується"),
            BotCommand(command="stats", description="Статистика"),
            BotCommand(command="help", description="Допомога"),
            BotCommand(command="myid", description="Мій Telegram ID"),
        ]
    )
    if settings.webapp_url.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Склад", web_app=WebAppInfo(url=settings.webapp_url + "/"))
        )
