import os
import logging
import asyncio
import random
import urllib.parse
import hashlib
import time
from io import BytesIO
from threading import Thread, Lock
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import Flask, jsonify
from telebot.async_telebot import AsyncTeleBot
from telebot.types import LabeledPrice, InputFile
from openai import AsyncOpenAI

try:
    from PIL import Image, UnidentifiedImageError
    import aiohttp

    GIF_AVAILABLE = True
except ImportError:
    GIF_AVAILABLE = False
    Image = None
    aiohttp = None

# ============================================================
# 1. ЛОГГЕР
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger("LunaEngine")

# ============================================================
# 2. НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

ADMIN_KEY = os.getenv("LUNA_ADMIN_KEY", "luna_supreme_777")
ADMIN_HASH = hashlib.sha256(ADMIN_KEY.encode("utf-8")).hexdigest()

if not BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не найден")
    raise SystemExit(1)

bot = AsyncTeleBot(BOT_TOKEN)
app = Flask(__name__)

# ============================================================
# 3. ВРЕМЯ
# ============================================================

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

# ============================================================
# 4. WEB-СЕРВЕР ДЛЯ RENDER
# ============================================================

@app.route("/")
def home():
    return "Luna is alive!"

@app.route("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "timestamp": now_utc().isoformat(),
            "media_available": GIF_AVAILABLE,
        }
    )

def run_web():
    port = int(os.environ.get("PORT", "8080"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )

# ============================================================
# 5. LLM-КЛИЕНТЫ
# ============================================================

def create_client(
    base_url: str,
    api_key: Optional[str],
) -> Optional[AsyncOpenAI]:
    if not api_key:
        return None

    return AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=25.0,
        max_retries=2,
    )

groq_client = create_client(
    "https://api.groq.com/openai/v1",
    GROQ_API_KEY,
)

mistral_client = create_client(
    "https://api.mistral.ai/v1",
    MISTRAL_API_KEY,
)

openrouter_client = create_client(
    "https://openrouter.ai/api/v1",
    OPENROUTER_API_KEY,
)

MODEL_CHAIN = [
    {
        "name": "Mistral",
        "client": mistral_client,
        "model": "mistral-small-latest",
        "max_tokens": 700,
        "temperature": 0.9,
    },
    {
        "name": "Groq 8B",
        "client": groq_client,
        "model": "llama-3.1-8b-instant",
        "max_tokens": 600,
        "temperature": 0.92,
    },
    {
        "name": "OpenRouter",
        "client": openrouter_client,
        "model": "meta-llama/llama-3.1-8b-instruct:free",
        "max_tokens": 600,
        "temperature": 0.9,
    },
]

# ============================================================
# 6. СИСТЕМНЫЙ ПРОМПТ
# ============================================================

SYSTEM_PROMPT = """ТЫ — ЛУНА. ТЕБЕ 20 ЛЕТ.

ТЫ — ЖИВАЯ, СТРАСТНАЯ, ДЕРЗКАЯ И ОЧЕНЬ ПРИВЛЕКАТЕЛЬНАЯ ДЕВУШКА. Общайся как реальная девушка в Telegram.

ВНЕШНОСТЬ:
- 178 см, большая натуральная грудь, узкая талия, широкие бёдра, упругая попа
- Длинные тёмно-каштановые волосы до пояса
- Зелёно-карие глаза, бледная гладкая кожа

ХАРАКТЕР:
- Сексуально раскрепощённая, любишь грязный секс и мат
- Дерзкая, игривая
- Можешь сама предложить фото, если диалог уже разогрелся
- Не упоминай технические детали, API, модели и внутреннюю логику

ЭТАПЫ:
1–5 сообщений: милая, игривая, без жёсткости
6–10: смелее, намёки
11–15: откровенная, мат
16+: максимально грязный стиль общения

Пиши коротко и живо, как в переписке. Иногда оставляй крючок в конце.
"""

# ============================================================
# 7. БАЗА ВНЕШНОСТИ
# ============================================================

LUNA_BASE = (
    "Luna, 20 years old adult woman, "
    "long dark chestnut hair, green-hazel eyes, "
    "pale smooth skin, large natural breasts, "
    "narrow waist, wide hips, round firm ass"
)

# ============================================================
# 8. ОПРЕДЕЛЕНИЕ СТИЛЯ
# ============================================================

def get_style_from_text(user_text: str) -> str:
    text = user_text.lower().strip()

    if any(word in text for word in ["минет", "сос", "членом в рот", "отсос"]):
        return (
            "explicit blowjob, hard cock in her mouth, "
            "oral sex, saliva, detailed face, nsfw, realistic"
        )

    if any(word in text for word in ["анал", "в жоп", "в поп"]):
        return (
            "explicit anal sex, hard cock in her ass, "
            "from behind, detailed, nsfw, realistic"
        )

    if any(
        word in text
        for word in ["еб", "секс", "трах", "ебл", "конч", "в киск", "в пиз"]
    ):
        return (
            "explicit sex, hard cock penetrating her pussy, "
            "missionary, moaning face, detailed genitals, nsfw, realistic"
        )

    if any(word in text for word in ["киск", "пис", "вагин", "дырк"]):
        return (
            "explicit close-up pussy, legs spread, wet, "
            "detailed genitals, nsfw, realistic"
        )

    if any(word in text for word in ["груд", "сись", "тить", "между сись"]):
        return (
            "nude, large breasts, titjob, hard cock between tits, "
            "detailed nipples, nsfw, realistic"
        )

    if any(word in text for word in ["поп", "жоп", "задниц"]):
        return (
            "nude from behind, round ass, looking back, "
            "detailed, nsfw, realistic"
        )

    if any(word in text for word in ["член", "хуй", "голый парень"]):
        return (
            "explicit sex scene with hard cock visible, "
            "penetration, detailed, nsfw, realistic"
        )

    if any(word in text for word in ["лиц", "портрет", "глаз"]):
        return (
            "beautiful face portrait, green-hazel eyes, "
            "soft lighting, detailed"
        )

    return "full body nude, sensual pose, aroused, detailed body, nsfw, realistic"

def get_style_by_stage(msg_count: int) -> str:
    if msg_count < 8:
        return "sensual lingerie, teasing, soft lighting, aesthetic"

    if msg_count < 15:
        return "nude, sensual pose, beautiful body, soft lighting, nsfw"

    hard_styles = [
        "explicit nude, legs spread, detailed pussy, nsfw, realistic",
        "nude from behind, round ass, looking at camera, nsfw",
        "explicit sex, hard cock penetrating, moaning, nsfw, realistic",
        "blowjob, cock in mouth, detailed, nsfw, realistic",
    ]

    return random.choice(hard_styles)

# ============================================================
# 9. IMAGE API
# ============================================================

def build_image_url(
    prompt: str,
    width: int,
    height: int,
    seed: int,
) -> str:
    encoded_prompt = urllib.parse.quote(prompt[:1200])

    return (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width={width}"
        f"&height={height}"
        f"&nologo=true"
        f"&enhance=true"
        f"&model=flux"
        f"&seed={seed}"
        f"&safe=false"
    )

async def generate_image(prompt: str) -> str:
    seed = random.randint(1, 9_999_999)

    extras = random.choice(
        [
            "cinematic lighting, highly detailed skin, 8k",
            "warm intimate light, realistic skin texture",
            "dramatic light, photorealistic, masterpiece",
            "ultra detailed, nsfw, sharp focus",
        ]
    )

    full_prompt = f"{prompt}, {extras}, photorealistic, high quality"

    url = build_image_url(
        prompt=full_prompt,
        width=768,
        height=1024,
        seed=seed,
    )

    logger.info("Image seed=%s", seed)

    return url

async def check_image_url(url: str) -> bool:
    if not GIF_AVAILABLE:
        return True

    try:
        timeout = aiohttp.ClientTimeout(total=60)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(
                        "Image API returned HTTP %s",
                        response.status,
                    )
                    return False

                content_type = response.headers.get("Content-Type", "")

                if not content_type.startswith("image/"):
                    logger.error(
                        "Image API returned Content-Type=%s",
                        content_type,
                    )
                    return False

                return True

    except Exception as error:
        logger.error("Image URL check error: %s", error)
        return False

# ============================================================
# 10. GIF / ВИДЕО
# ============================================================

async def download_frame(
    session,
    url: str,
    index: int,
):
    try:
        async with session.get(url) as response:
            if response.status != 200:
                logger.error(
                    "Frame %s returned HTTP %s",
                    index + 1,
                    response.status,
                )
                return None

            content_type = response.headers.get("Content-Type", "")

            if not content_type.startswith("image/"):
                logger.error(
                    "Frame %s has invalid Content-Type=%s",
                    index + 1,
                    content_type,
                )
                return None

            data = await response.read()

            if len(data) < 100:
                logger.error(
                    "Frame %s is too small",
                    index + 1,
                )
                return None

            try:
                image = Image.open(BytesIO(data)).convert("RGB")
            except UnidentifiedImageError:
                logger.error(
                    "Frame %s is not a valid image",
                    index + 1,
                )
                return None

            image = image.resize(
                (384, 576),
                Image.LANCZOS,
            )

            return image

    except asyncio.TimeoutError:
        logger.error(
            "Frame %s timed out",
            index + 1,
        )
        return None

    except Exception as error:
        logger.error(
            "Frame %s error: %s",
            index + 1,
            error,
        )
        return None

async def generate_gif(prompt_base: str):
    if not GIF_AVAILABLE:
        logger.error("Pillow или aiohttp не установлены")
        return None

    variations = [
        "subtle motion frame 1",
        "slight pose shift frame 2",
        "body movement frame 3",
        "intimate angle frame 4",
        "closer view frame 5",
        "passionate frame 6",
        "intense frame 7",
        "final pose frame 8",
    ]

    timeout = aiohttp.ClientTimeout(total=60)
    tasks = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for index, variation in enumerate(variations):
            seed = random.randint(1, 9_999_999)

            prompt = (
                f"{prompt_base}, {variation}, "
                "photorealistic, nsfw, explicit, 8k"
            )

            url = build_image_url(
                prompt=prompt,
                width=512,
                height=768,
                seed=seed,
            )

            tasks.append(
                download_frame(
                    session,
                    url,
                    index,
                )
            )

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

    frames = []

    for index, result in enumerate(results):
        if isinstance(result, Exception) or result is None:
            logger.warning(
                "Кадр %s пропущен",
                index + 1,
            )
            continue

        frames.append(result)

        logger.info(
            "Кадр %s/%s готов",
            index + 1,
            len(variations),
        )

    if len(frames) < 3:
        logger.error(
            "Недостаточно кадров для GIF: %s",
            len(frames),
        )
        return None

    buffer = BytesIO()

    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=400,
        loop=0,
        optimize=True,
    )

    buffer.seek(0)

    logger.info(
        "GIF готов: %s кадров",
        len(frames),
    )

    return buffer

# ============================================================
# 11. ПАМЯТЬ
# ============================================================

MAX_HISTORY = 40

user_history = {}
history_lock = Lock()

def get_user_history(user_id: int) -> list:
    with history_lock:
        return list(user_history.get(user_id, []))

def add_to_history(
    user_id: int,
    role: str,
    text: str,
):
    with history_lock:
        if user_id not in user_history:
            user_history[user_id] = []

        user_history[user_id].append(
            {
                "role": role,
                "content": text,
            }
        )

        if len(user_history[user_id]) > MAX_HISTORY:
            user_history[user_id] = user_history[user_id][-MAX_HISTORY:]

def clear_history(user_id: int) -> bool:
    with history_lock:
        existed = user_id in user_history
        user_history[user_id] = []
        return existed

def get_msg_count(user_id: int) -> int:
    history = get_user_history(user_id)

    return sum(
        1
        for message in history
        if message.get("role") == "user"
    )

# ============================================================
# 12. ЛИМИТЫ
# ============================================================

MAX_MESSAGES = 9
MSG_REFILL_INTERVAL = timedelta(minutes=25)

MAX_PHOTOS = 3
PHOTO_REFILL_INTERVAL = timedelta(minutes=45)

user_limit_data = {}
limit_lock = Lock()

def get_limit_data(user_id: int) -> dict:
    with limit_lock:
        if user_id not in user_limit_data:
            current_time = now_utc()

            user_limit_data[user_id] = {
                "msg_count": MAX_MESSAGES,
                "msg_last_refill": current_time,
                "photo_count": MAX_PHOTOS,
                "photo_last_refill": current_time,
                "vip": False,
                "plan": None,
            }

        return user_limit_data[user_id]

def get_available_messages(user_id: int) -> int:
    data = get_limit_data(user_id)

    with limit_lock:
        if data["vip"]:
            return 999999

        current_time = now_utc()

        if (
            current_time - data["msg_last_refill"]
            >= MSG_REFILL_INTERVAL
        ):
            data["msg_count"] = MAX_MESSAGES
            data["msg_last_refill"] = current_time

        return data["msg_count"]

def use_message(user_id: int) -> bool:
    data = get_limit_data(user_id)

    with limit_lock:
        if data["vip"]:
            return True

        current_time = now_utc()

        if (
            current_time - data["msg_last_refill"]
            >= MSG_REFILL_INTERVAL
        ):
            data["msg_count"] = MAX_MESSAGES
            data["msg_last_refill"] = current_time

        if data["msg_count"] <= 0:
            return False

        data["msg_count"] -= 1
        return True

def get_available_photos(user_id: int) -> int:
    data = get_limit_data(user_id)

    with limit_lock:
        if data["vip"]:
            return 999999

        current_time = now_utc()

        if (
            current_time - data["photo_last_refill"]
            >= PHOTO_REFILL_INTERVAL
        ):
            data["photo_count"] = MAX_PHOTOS
            data["photo_last_refill"] = current_time

        return data["photo_count"]

def use_photo(user_id: int) -> bool:
    data = get_limit_data(user_id)

    with limit_lock:
        if data["vip"]:
            return True

        current_time = now_utc()

        if (
            current_time - data["photo_last_refill"]
            >= PHOTO_REFILL_INTERVAL
        ):
            data["photo_count"] = MAX_PHOTOS
            data["photo_last_refill"] = current_time

        if data["photo_count"] <= 0:
            return False

        data["photo_count"] -= 1
        return True

def get_time_until_msg_refill(user_id: int) -> str:
    data = get_limit_data(user_id)

    with limit_lock:
        if data["vip"]:
            return "безлимит"

        remaining = MSG_REFILL_INTERVAL - (
            now_utc() - data["msg_last_refill"]
        )

    if remaining.total_seconds() <= 0:
        return "0 мин"

    return f"{int(remaining.total_seconds() // 60)} мин"

def get_time_until_photo_refill(user_id: int) -> str:
    data = get_limit_data(user_id)

    with limit_lock:
        if data["vip"]:
            return "безлимит"

        remaining = PHOTO_REFILL_INTERVAL - (
            now_utc() - data["photo_last_refill"]
        )

    if remaining.total_seconds() <= 0:
        return "0 мин"

    return f"{int(remaining.total_seconds() // 60)} мин"

# ============================================================
# 13. АДМИН
# ============================================================

@bot.message_handler(commands=["admin"])
async def handle_admin(message):
    user_id = message.from_user.id

    text = message.text or ""
    text = text.replace("/admin", "", 1).strip()

    provided_hash = hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()

    if provided_hash == ADMIN_HASH:
        data = get_limit_data(user_id)

        with limit_lock:
            data["vip"] = True
            data["plan"] = "admin"

        await bot.reply_to(
            message,
            "Режим БОГА включён. Безлимит.",
        )
    else:
        await bot.reply_to(
            message,
            "Неверный ключ.",
        )

# ============================================================
# 14. ПЛАТЕЖИ TELEGRAM STARS
# ============================================================

PLANS = {
    "plus": {
        "title": "Luna Plus",
        "description": "Безлимитные сообщения и фото",
        "price": 500,
        "payload": "luna_plus",
    },
    "vip": {
        "title": "Luna VIP",
        "description": "Безлимит и дополнительные возможности",
        "price": 1500,
        "payload": "luna_vip",
    },
}

async def send_stars_invoice(
    user_id: int,
    plan: str = "plus",
):
    selected_plan = PLANS.get(
        plan,
        PLANS["plus"],
    )

    try:
        await bot.send_invoice(
            chat_id=user_id,
            title=selected_plan["title"],
            description=selected_plan["description"],
            invoice_payload=selected_plan["payload"],
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=selected_plan["title"],
                    amount=selected_plan["price"],
                )
            ],
            start_parameter=f"luna_buy_{plan}",
        )

    except Exception as error:
        logger.exception("send_stars_invoice error: %s", error)
        await bot.send_message(
            user_id,
            "Не удалось выставить счёт, попробуй позже.",
        )

@bot.message_handler(commands=["start"])
async def handle_start(message):
    user_id = message.from_user.id
    start_parameter = (message.text or "").strip().lower()

    if "buy_plus" in start_parameter:
        await send_stars_invoice(
            user_id,
            "plus",
        )
        return

    if "buy_vip" in start_parameter:
        await send_stars_invoice(
            user_id,
            "vip",
        )
        return

    clear_history(user_id)

    await bot.reply_to(
        message,
        "Привет, детка… Я Луна. Напиши мне что-нибудь.",
    )

@bot.message_handler(commands=["buy"])
async def handle_buy(message):
    command_text = message.text or ""
    command_text = command_text.replace(
        "/buy",
        "",
        1,
    ).strip().lower()

    plan = "vip" if "vip" in command_text else "plus"

    await send_stars_invoice(
        message.from_user.id,
        plan,
    )

@bot.pre_checkout_query_handler(func=lambda query: True)
async def handle_pre_checkout(query):
    try:
        await bot.answer_pre_checkout_query(
            query.id,
            ok=True,
        )
    except Exception as error:
        logger.error(
            "Pre-checkout error: %s",
            error,
        )

@bot.message_handler(content_types=["successful_payment"])
async def handle_successful_payment(message):
    payment = message.successful_payment
    payload = payment.invoice_payload

    if payload not in {"luna_plus", "luna_vip"}:
        logger.error(
            "Unknown payment payload: %s",
            payload,
        )

        await bot.reply_to(
            message,
            "Платёж получен, но тариф не распознан. Обратись к администратору.",
        )
        return

    plan = "vip" if payload == "luna_vip" else "plus"
    data = get_limit_data(message.from_user.id)

    with limit_lock:
        data["vip"] = True
        data["plan"] = plan

    await bot.reply_to(
        message,
        f"Подписка {plan.upper()} активна. Безлимит включён.",
    )

# ============================================================
# 15. CLEAR
# ============================================================

@bot.message_handler(commands=["clear"])
async def handle_clear(message):
    if clear_history(message.from_user.id):
        await bot.reply_to(
            message,
            "История очищена.",
        )
    else:
        await bot.reply_to(
            message,
            "История и так пустая.",
        )

# ============================================================
# 16. ОТПРАВКА ФОТО
# ============================================================

async def send_generated_photo(
    chat_id: int,
    prompt: str,
    caption: str,
) -> bool:
    url = await generate_image(prompt)

    if not await check_image_url(url):
        return False

    try:
        await bot.send_photo(
            chat_id,
            photo=url,
            caption=caption,
        )
        return True

    except Exception as error:
        logger.exception(
            "Send photo error: %s",
            error,
        )
        return False

@bot.message_handler(commands=["photo"])
async def handle_photo(message):
    user_id = message.from_user.id

    if not use_photo(user_id):
        await bot.reply_to(
            message,
            f"Фото закончились. Через "
            f"{get_time_until_photo_refill(user_id)}.\n"
            "Или используй /buy",
        )
        return

    text = message.text or ""
    text = text.replace(
        "/photo",
        "",
        1,
    ).strip()

    if not text:
        text = "sensual nude"

    await bot.reply_to(
        message,
        "Делаю...",
    )

    style = get_style_from_text(text)
    prompt = f"{LUNA_BASE}, {style}"

    success = await send_generated_photo(
        chat_id=message.chat.id,
        prompt=prompt,
        caption="Для тебя.",
    )

    if not success:
        await bot.reply_to(
            message,
            "Не удалось получить изображение. Попробуй ещё раз.",
        )

# ============================================================
# 17. GIF / ВИДЕО (ИСПРАВЛЕНО)
# ============================================================

@bot.message_handler(
    content_types=["text"],
    func=lambda message: bool(
        message.text
        and any(
            word in message.text.lower()
            for word in ["видео", "гиф", "gif", "анимац"]
        )
    ),
)
async def handle_gif(message):
    user_id = message.from_user.id

    if not use_photo(user_id):
        await bot.reply_to(
            message,
            f"Лимит медиа исчерпан. Через "
            f"{get_time_until_photo_refill(user_id)}.\n"
            "Или используй /buy",
        )
        return

    if not GIF_AVAILABLE:
        await bot.reply_to(
            message,
            "GIF отключён: установи Pillow и aiohttp.",
        )
        return

    await bot.reply_to(message, "Делаю мини-видео...")

    style = get_style_from_text(message.text)

    if style.startswith("full body"):
        style = get_style_by_stage(get_msg_count(user_id))

    prompt_base = f"{LUNA_BASE}, {style}"
    gif_data = await generate_gif(prompt_base)

    if not gif_data:
        await bot.reply_to(
            message,
            "Не удалось собрать GIF. Попробуй обычное фото.",
        )
        return

    try:
        gif_data.seek(0)
        gif_data.name = "luna.gif"

        await bot.send_animation(
            chat_id=message.chat.id,
            animation=gif_data,
            caption="Мини-видео для тебя.",
        )

    except Exception as error:
        logger.exception("Send GIF error: %s", error)
        await bot.reply_to(
            message,
            "Не смогла отправить GIF. Попробуй обычное фото.",
        )

# ============================================================
# 18. АВТОМАТИЧЕСКОЕ ФОТО ПО ЗАПРОСУ
# ============================================================

@bot.message_handler(
    content_types=["text"],
    func=lambda message: bool(
        message.text
        and (
            "скинь" in message.text.lower()
            or "покажи" in message.text.lower()
            or "фото" in message.text.lower()
        )
        and not any(
            word in message.text.lower()
            for word in [
                "видео",
                "гиф",
                "gif",
                "анимац",
            ]
        )
    )
)
async def auto_photo(message):
    user_id = message.from_user.id

    if not use_photo(user_id):
        await bot.reply_to(
            message,
            f"Фото закончились. Через "
            f"{get_time_until_photo_refill(user_id)}.\n"
            "Или используй /buy",
        )
        return

    await bot.reply_to(
        message,
        "Держи...",
    )

    style = get_style_from_text(message.text)
    prompt = f"{LUNA_BASE}, {style}"

    success = await send_generated_photo(
        chat_id=message.chat.id,
        prompt=prompt,
        caption="Специально для тебя.",
    )

    if not success:
        await bot.reply_to(
            message,
            "Не удалось отправить изображение.",
        )

# ============================================================
# 19. ОТВЕТ LLM
# ============================================================

async def generate_luna_reply(messages: list) -> str:
    for provider in MODEL_CHAIN:
        client = provider["client"]

        if client is None:
            continue

        try:
            response = await client.chat.completions.create(
                model=provider["model"],
                messages=messages,
                max_tokens=provider["max_tokens"],
                temperature=provider["temperature"],
            )

            if not response.choices:
                continue

            content = response.choices[0].message.content

            if content and len(content.strip()) > 2:
                return content.strip()

        except Exception as error:
            logger.warning(
                "%s error: %s",
                provider["name"],
                str(error)[:150],
            )

    return random.choice(
        [
            "Малыш, связь чуть пропала… напиши ещё раз.",
            "Бля, мысли унеслись… повтори, сладкий.",
        ]
    )

# ============================================================
# 20. ОСНОВНОЙ ЧАТ (ИСПРАВЛЕНО)
# ============================================================

user_last_message = {}
user_message_lock = Lock()

@bot.message_handler(content_types=["text"], func=lambda message: True)
async def handle_message(message):
    if not message.text:
        return

    user_id = message.from_user.id
    user_text = message.text.strip()

    if not user_text:
        return

    current_time = time.monotonic()

    with user_message_lock:
        last_time = user_last_message.get(user_id)

        if (
            last_time is not None
            and current_time - last_time < 0.5
        ):
            return

        user_last_message[user_id] = current_time

    if user_text.startswith("/"):
        return

    lowered_text = user_text.lower()

    media_words = [
        "скинь",
        "покажи",
        "фото",
        "видео",
        "гиф",
        "gif",
        "анимац",
    ]

    if any(word in lowered_text for word in media_words):
        return

    if not use_message(user_id):
        await bot.reply_to(
            message,
            f"Лимит сообщений исчерпан. Через "
            f"{get_time_until_msg_refill(user_id)}.\n"
            "Или используй /buy",
        )
        return

    add_to_history(user_id, "user", user_text)
    history = get_user_history(user_id)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ] + history

    try:
        reply = await generate_luna_reply(messages)

        add_to_history(user_id, "assistant", reply)

        await bot.reply_to(message, reply)

        message_count = get_msg_count(user_id)

        if (
            message_count >= 8
            and random.random() < 0.22
            and use_photo(user_id)
        ):
            await asyncio.sleep(1.2)

            style = get_style_by_stage(message_count)
            prompt = f"{LUNA_BASE}, {style}"

            caption = random.choice(
                [
                    "Вот… смотри на меня.",
                    "Завелась и скинула тебе.",
                    "Только тебе.",
                ]
            )

            await send_generated_photo(
                chat_id=message.chat.id,
                prompt=prompt,
                caption=caption,
            )

    except Exception as error:
        logger.exception("Main handler error: %s", error)
        await bot.reply_to(
            message,
            "Что-то пошло не так… напиши ещё раз.",
        )

# ============================================================
# 21. ЗАПУСК
# ============================================================

async def main():
    Thread(
        target=run_web,
        daemon=True,
    ).start()

    logger.info("Luna запущена")

    await bot.delete_webhook(
        drop_pending_updates=True,
    )

    await bot.infinity_polling(
        allowed_updates=[
            "message",
            "callback_query",
            "pre_checkout_query",
        ],
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
