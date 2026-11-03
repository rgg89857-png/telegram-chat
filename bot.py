
import os
import re
import asyncio
import logging
from urllib.parse import urlparse, unquote

import aiohttp
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Настройки Render
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

demo_enabled = False


def get_gift_title(url: str) -> str:
    slug = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
    match = re.match(r"(.+)-(\d+)$", slug)

    if match:
        name = match.group(1).replace("-", " ")
        return f"{name.title()} #{match.group(2)}"

    return slug.replace("-", " ").title() or "Telegram Gift"


async def get_gift_image(url: str):
    try:
        timeout = aiohttp.ClientTimeout(total=8)

        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as session:
            async with session.get(
                url, allow_redirects=True
            ) as response:
                if response.status != 200:
                    return None

                soup = BeautifulSoup(
                    await response.text(), "html.parser"
                )

                for attr in ("og:image", "twitter:image"):
                    tag = soup.find("meta", property=attr)

                    if tag is None:
                        tag = soup.find(
                            "meta", attrs={"name": attr}
                        )

                    if tag and tag.get("content"):
                        image_url = tag["content"]
                        parsed = urlparse(image_url)

                        if (
                            parsed.scheme == "https"
                            and parsed.netloc
                        ):
                            return image_url

    except Exception:
        logging.exception("Не удалось загрузить превью NFT")

    return None


def demo_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Decline",
                    callback_data="demo_decline",
                ),
                InlineKeyboardButton(
                    text="☑️ Accept",
                    callback_data="demo_accept",
                ),
            ]
        ]
    )


@dp.message(F.text.regexp(r"^/WellBuy(?:@\w+)?$"))
async def toggle_demo(message: Message):
    global demo_enabled

    if not message.from_user:
        return

    if message.from_user.id != OWNER_ID:
        return

    demo_enabled = not demo_enabled

    status = "ВКЛЮЧЕН" if demo_enabled else "ВЫКЛЮЧЕН"
    await message.answer(f"🎬 Режим DEMO {status}.")


@dp.message(F.text.regexp(r"^\.buy(?:\s|$)"))
async def create_demo_offer(message: Message):
    if not message.from_user:
        return

    if message.from_user.id != OWNER_ID:
        return

    if not demo_enabled:
        await message.reply(
            "Сначала включи режим командой /WellBuy."
        )
        return

    parts = message.text.split()

    if len(parts) != 4:
        await message.reply(
            "Формат команды:\n"
            "<code>.buy ссылка цена stars</code>\n"
            "или\n"
            "<code>.buy ссылка цена gram</code>"
        )
        return

    _, url, price_text, currency = parts

    parsed = urlparse(url)

    if (
        parsed.scheme != "https"
        or parsed.hostname != "t.me"
        or not parsed.path.startswith("/nft/")
        or not parsed.path.removeprefix("/nft/").strip("/")
    ):
        await message.reply(
            "Нужна ссылка на Telegram NFT, например:\n"
            "<code>https://t.me/nft/PlushPepe-1941</code>"
        )
        return

    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", price_text):
        await message.reply("Укажи корректную цену.")
        return

    if float(price_text) <= 0:
        await message.reply("Цена должна быть больше нуля.")
        return

    currency = currency.lower()

    if currency not in ("stars", "gram"):
        await message.reply("Валюта должна быть stars или gram.")
        return

    title = get_gift_title(url)
    image_url = await get_gift_image(url)

    amount = (
        f"{price_text} STARS"
        if currency == "stars"
        else f"{price_text} GRAM"
    )

    caption = (
        "🎬 <b>DEMO • НЕ НАСТОЯЩИЙ ОФФЕР</b>\n\n"
        "<b>Telegram</b>\n"
        f"<b>{title}</b>\n\n"
        "The user is offering you\n"
        f"<b>{amount}</b> for the gift "
        f'<a href="{url}">{title}</a>.\n\n'
        "⏳ The demo offer is valid for another 6 hours.\n\n"
        "<i>Макет для видеосъёмки. "
        "Сделка и перевод средств не выполняются.</i>"
    )

    try:
        if image_url:
            await message.answer_photo(
                photo=image_url,
                caption=caption,
                reply_markup=demo_keyboard(),
            )
        else:
            await message.answer(
                caption,
                reply_markup=demo_keyboard(),
                disable_web_page_preview=False,
            )

    except Exception:
        logging.exception("Ошибка отправки карточки")

        await message.answer(
            caption,
            reply_markup=demo_keyboard(),
        )


@dp.callback_query(
    F.data.in_({"demo_accept", "demo_decline"})
)
async def demo_button(callback: CallbackQuery):
    action = (
        "Accept"
        if callback.data == "demo_accept"
        else "Decline"
    )

    await callback.answer(
        f"DEMO: нажата кнопка {action}. "
        "Настоящая сделка не выполняется.",
        show_alert=True,
    )


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN.")

    if OWNER_ID == 0:
        raise RuntimeError("Не задан OWNER_ID.")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
