"""Перевірка підпису initData, який Telegram передає у Mini App.

Алгоритм (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):
  secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
  hash       = HMAC_SHA256(key=secret_key, msg=data_check_string)
де data_check_string — усі поля, крім hash, відсортовані за ключем, у форматі key=value
через перенесення рядка.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(Exception):
    pass


def _calc_hash(fields: dict[str, str], bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()


def validate_init_data(init_data: str, bot_token: str, max_age: int | None = 86400) -> dict:
    """Повертає dict з полями initData (user уже розпарсений) або кидає InitDataError."""
    if not init_data:
        raise InitDataError("initData порожній")
    if not bot_token:
        raise InitDataError("BOT_TOKEN не налаштований")
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError as exc:
        raise InitDataError("Некоректний initData") from exc

    received = fields.pop("hash", None)
    if not received:
        raise InitDataError("У initData немає hash")

    ok = hmac.compare_digest(_calc_hash(fields, bot_token), received)
    if not ok and "signature" in fields:
        # Поле signature (Ed25519) з'явилось у новіших клієнтах; деякі реалізації
        # виключають його з data_check_string. Перевіряємо обидва варіанти.
        without_sig = {k: v for k, v in fields.items() if k != "signature"}
        ok = hmac.compare_digest(_calc_hash(without_sig, bot_token), received)
    if not ok:
        raise InitDataError("Невірний підпис initData")

    if max_age is not None:
        try:
            auth_date = int(fields.get("auth_date", "0"))
        except ValueError as exc:
            raise InitDataError("Некоректний auth_date") from exc
        if time.time() - auth_date > max_age:
            raise InitDataError("initData застарів, перевідкрийте застосунок")

    result: dict = dict(fields)
    if "user" in fields:
        try:
            result["user"] = json.loads(fields["user"])
        except json.JSONDecodeError as exc:
            raise InitDataError("Некоректне поле user") from exc
    return result


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Допоміжна функція для тестів: формує валідний initData."""
    from urllib.parse import urlencode

    data = dict(fields)
    data["hash"] = _calc_hash(fields, bot_token)
    return urlencode(data)
