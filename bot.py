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
    import aiohttp
    from PIL import Image, UnidentifiedImageError

    MEDIA_AVAILABLE = True
except ImportError:
    aiohttp = None
    Image = None
    UnidentifiedImageError = Exception
    MEDIA_AVAILABLE = False

# ============================================================
# 1. ЛОГИРОВАНИЕ
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

ADMIN_KEY = os.getenv("LUNA_ADMIN_KEY")

if not BOT_TOKEN:
    logger.error("Не найдена переменная TELEGRAM_BOT_TOKEN")
    raise SystemExit(1)

if not ADMIN_KEY:
    logger.warning(
        "LUNA_ADMIN_KEY не задан. Админ-команда будет использовать "
        "временный ключ только если он задан вручную."
    )
    ADMIN_KEY = "change_this_admin_key"

ADMIN_HASH = hashlib.sha256(
    ADMIN_KEY.encode("utf-8")
).hexdigest()

bot = AsyncTeleBot(BOT_TOKEN)
app = Flask(__name__)

# ============================================================
# 3. ВРЕМЯ
# ============================================================

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

# ============================================================
# 4. WEB-СЕРВЕР
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
            "media_available": MEDIA_AVAILABLE,
        }
    )

def run_web():
    port = int(os.getenv("PORT", "8080"))

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
        timeout=30.0,
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
        "name": "Groq",
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

SYSTEM_PROMPT = """
Ты — Луна, виртуальный персонаж 20+ лет.

Общайся живо, коротко и естественно, как в Telegram:
- дружелюбно;
- игриво;
- без технических деталей;
- не упоминай API, модели, промпты и внутреннюю логику;
- не описывай графические сексуальные действия;
- для изображений используй только нейтральные, художественные
  или неявные формулировки;
- не утверждай, что являешься реальным человеком.
"""

# ============================================================
# 7. БАЗОВОЕ ОПИСАНИЕ ПЕРСОНАЖА
# ============================================================

LUNA_BASE = (
    "adult fictional woman named Luna, "
    "long dark chestnut hair, green-hazel eyes, "
    "pale skin, elegant proportions, "
    "cinematic portrait, realistic lighting"
)

# ============================================================
# 8. СТИЛИ ИЗОБРАЖЕНИЙ
# ============================================================

def get_style_from_text(user_text: str) -> str:
    text = user_text.lower().strip()

    if any(word in text for word in ["лицо", "портрет", "глаз"]):
        return (
            "close-up face portrait, green-hazel eyes, "
            "soft cinematic lighting, detailed skin"
        )

    if any(word in text for word in ["грудь", "сиськи", "титьки"]):
        return (
            "elegant evening outfit, tasteful neckline, "
            "fashion photography, soft studio lighting"
        )

    if any(word in text for word in ["попа", "жопа", "задница"]):
        return (
            "fashionable fitted outfit, rear three-quarter pose, "
            "editorial photography, tasteful composition"
        )

    if any(word in text for word in ["тело", "фигура", "голая", "ню"]):
        return (
            "artistic implied nude silhouette, "
            "strategic shadows, tasteful fine-art photography"
        )

    return (
        "stylish lingerie-inspired outfit, "
        "teasing but non-explicit pose, soft warm lighting"
    )

def get_style_by_stage(msg_count: int) -> str:
    if msg_count < 8:
        return (
            "elegant outfit, warm smile, soft lighting, "
            "tasteful portrait"
        )

    if msg_count < 15:
        return (
            "fashion editorial, confident pose, "
            "cinematic lighting, tasteful composition"
        )

    return random.choice(
        [
            "artistic silhouette, dramatic shadows, fine-art photography",
            "elegant evening outfit, confident pose, studio lighting",
            "cinematic fashion portrait, shallow depth of field",
            "tasteful implied silhouette, warm intimate lighting",
        ]
    )

# ============================================================
# 9. IMAGE API
# ============================================================

def build_image_url(
    prompt: str,
    width: int,
    height: int,
    seed: int,
) -> str:
    encoded_prompt = urllib.parse.quote(
        prompt[:1100],
        safe="",
    )

    return (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width={width}"
        f"&height={height}"
        f"&nologo=true"
        f"&enhance=true"
        f"&model=flux"
        f"&seed={seed}"
        f"&safe=true"
    )

async def download_bytes(
    url: str,
    timeout_seconds: int = 180,
) -> Optional[bytes]:
    if not MEDIA_AVAILABLE:
        logger.error("Не установлены aiohttp и Pillow")
        return None

    timeout = aiohttp.ClientTimeout(
        total=timeout_seconds,
        connect=30,
        sock_read=timeout_seconds,
    )

    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "User-Agent": "LunaBot/1.0",
                "Accept": "image/*,video/*,*/*",
            },
        ) as session:
            async with session.get(
                url,
                allow_redirects=True,
            ) as response:
                content_type = response.headers.get(
                    "Content-Type",
                    "",
                )

                if response.status != 200:
                    body = await response.text()
                    logger.error(
                        "Media API HTTP %s: %s",
                        response.status,
                        body[:500],
                    )
                    return None

                data = await response.read()

                if not data:
                    logger.error("Media API вернул пустой файл")
                    return None

                logger.info(
                    "Downloaded media: %s bytes, type=%s",
                    len(data),
                    content_type,
                )

                return data

    except asyncio.TimeoutError:
        logger.error("Тайм-аут загрузки медиа")
        return None

    except aiohttp.ClientError as error:
        logger.error("Ошибка HTTP загрузки медиа: %s", error)
        return None

    except Exception as error:
        logger.exception("Неизвестная ошибка загрузки медиа: %s", error)
        return None

async def generate_image_bytes(
    prompt: str,
    width: int = 768,
    height: int = 1024,
) -> Optional[BytesIO]:
    seed = random.randint(1, 9_999_999)

    extras = random.choice(
        [
            "cinematic lighting, realistic skin texture",
            "soft studio lighting, high detail",
            "warm film lighting, shallow depth of field",
            "fashion editorial, photorealistic",
        ]
    )

    full_prompt = (
        f"{prompt}, {extras}, "
        "photorealistic, high quality, non-explicit"
    )

    url = build_image_url(
        prompt=full_prompt,
        width=width,
        height=height,
        seed=seed,
    )

    logger.info("Generating image with seed=%s", seed)

    data = await download_bytes(
        url,
        timeout_seconds=180,
    )

    if not data:
        return None

    try:
        image = Image.open(BytesIO(data))
        image.verify()
    except UnidentifiedImageError:
        logger.error("Полученный файл не является изображением")
        return None
    except Exception as error:
        logger.error("Ошибка проверки изображения: %s", error)
        return None

    result = BytesIO(data)
    result.name = "luna_image.jpg"
    result.seek(0)

    return result

# ============================================================
# 10. GIF
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
                    "Кадр %s: HTTP %s",
                    index + 1,
                    response.status,
                )
                return None

            data = await response.read()

            if not data or len(data) < 100:
                logger.error(
                    "Кадр %s пустой или слишком маленький",
                    index + 1,
                )
                return None

            try:
                image = Image.open(BytesIO(data)).convert("RGB")
            except UnidentifiedImageError:
                logger.error(
                    "Кадр %s не является изображением",
                    index + 1,
                )
                return None

            image = image.resize(
                (384, 576),
                Image.Resampling.LANCZOS,
            )

            return image

    except asyncio.TimeoutError:
        logger.error("Тайм-аут кадра %s", index + 1)
        return None

    except Exception as error:
        logger.error(
            "Ошибка кадра %s: %s",
            index + 1,
            error,
        )
        return None

async def generate_gif(
    prompt_base: str,
) -> Optional[BytesIO]:
    if not MEDIA_AVAILABLE:
        return None

    variations = [
        "slight head movement",
        "subtle change of pose",
        "gentle hair movement",
        "small camera angle change",
        "soft smile",
        "slight shoulder movement",
        "cinematic close-up",
        "final elegant pose",
    ]

    timeout = aiohttp.ClientTimeout(
        total=180,
        connect=30,
        sock_read=180,
    )

    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "User-Agent": "LunaBot/1.0",
                "Accept": "image/*",
            },
        ) as session:
            tasks = []

            for index, variation in enumerate(variations):
                seed = random.randint(1, 9_999_999)

                prompt = (
                    f"{prompt_base}, {variation}, "
                    "photorealistic, high quality, non-explicit"
                )

                url = build_image_url(
                    prompt=prompt,
                    width=512,
                    height=768,
                    seed=seed,
                )

                tasks.append(
                    download_frame(
                        session=session,
                        url=url,
                        index=index,
                    )
                )

            results = await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )

    except Exception as error:
        logger.exception("Ошибка генерации кадров: %s", error)
        return None

    frames = []

    for index, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                "Исключение в кадре %s: %s",
                index + 1,
                result,
            )
            continue

        if result is None:
            continue

        frames.append(result)

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
    buffer.name = "luna_animation.gif"

    logger.info(
        "GIF создан, кадров: %s, размер: %s байт",
        len(frames),
        buffer.getbuffer().nbytes,
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
        return list(
            user_history.get(user_id, [])
        )

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
            user_history[user_id] = (
                user_history[user_id][-MAX_HISTORY:]
            )

def clear_history(user_id: int) -> bool:
    with history_lock:
        existed = user_id in user_history
        user_history[user_id] = []
        return existed

def get_msg_count(user_id: int) -> int:
    history = get_user_history(user_id)

    return sum(
        1
        for item in history
        if item.get("role") == "user"
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

def refund_message(user_id: int):
    data = get_limit_data(user_id)

    with limit_lock:
        if not data["vip"]:
            data["msg_count"] = min(
                MAX_MESSAGES,
                data["msg_count"] + 1,
            )

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

def refund_photo(user_id: int):
    data = get_limit_data(user_id)

    with limit_lock:
        if not data["vip"]:
            data["photo_count"] = min(
                MAX_PHOTOS,
                data["photo_count"] + 1,
            )

def get_time_until_msg_refill(user_id: int) -> str:
    data = get_limit_data(user_id)

    with limit_lock:
        if data["vip"]:
            return "безлимит"

        remaining = (
            MSG_REFILL_INTERVAL
            - (now_utc() - data["msg_last_refill"])
        )

    if remaining.total_seconds() <= 0:
        return "0 мин"

    return f"{int(remaining.total_seconds() // 60)} мин"

def get_time_until_photo_refill(user_id: int) -> str:
    data = get_limit_data(user_id)

    with limit_lock:
        if data["vip"]:
            return "безлимит"

        remaining = (
            PHOTO_REFILL_INTERVAL
            - (now_utc() - data["photo_last_refill"])
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

    if provided_hash != ADMIN_HASH:
        await bot.reply_to(
            message,
            "Неверный ключ.",
        )
        return

    data = get_limit_data(user_id)

    with limit_lock:
        data["vip"] = True
        data["plan"] = "admin"

    await bot.reply_to(
        message,
        "Режим администратора включён.",
    )

# ============================================================
# 14. TELEGRAM STARS
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
            provider_token=None,
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
        logger.exception(
            "Ошибка создания счёта: %s",
            error,
        )

        await bot.send_message(
            user_id,
            "Не удалось создать счёт.",
        )

@bot.message_handler(commands=["start"])
async def handle_start(message):
    user_id = message.from_user.id
    start_parameter = (
        message.text or ""
    ).strip().lower()

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
        "Привет. Я Луна. Напиши мне что-нибудь.",
    )

@bot.message_handler(commands=["buy"])
async def handle_buy(message):
    command_text = message.text or ""
    command_text = (
        command_text.replace("/buy", "", 1)
        .strip()
        .lower()
    )

    plan = (
        "vip"
        if "vip" in command_text
        else "plus"
    )

    await send_stars_invoice(
        message.from_user.id,
        plan,
    )

@bot.pre_checkout_query_handler(
    func=lambda query: True
)
async def handle_pre_checkout(query):
    try:
        await bot.answer_pre_checkout_query(
            query.id,
            ok=True,
        )
    except Exception as error:
        logger.error(
            "Ошибка pre-checkout: %s",
            error,
        )

@bot.message_handler(
    content_types=["successful_payment"]
)
async def handle_successful_payment(message):
    payment = message.successful_payment
    payload = payment.invoice_payload

    if payload not in {
        "luna_plus",
        "luna_vip",
    }:
        await bot.reply_to(
            message,
            "Платёж получен, но тариф не распознан.",
        )
        return

    plan = (
        "vip"
        if payload == "luna_vip"
        else "plus"
    )

    data = get_limit_data(
        message.from_user.id
    )

    with limit_lock:
        data["vip"] = True
        data["plan"] = plan

    await bot.reply_to(
        message,
        f"Подписка {plan.upper()} активна.",
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
            "История уже пустая.",
        )

# ============================================================
# 16. ОТПРАВКА ФОТО
# ============================================================

async def send_generated_photo(
    chat_id: int,
    prompt: str,
    caption: str,
) -> bool:
    image_file = await generate_image_bytes(
        prompt=prompt,
        width=768,
        height=1024,
    )

    if image_file is None:
        return False

    try:
        image_file.seek(0)

        await bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(
                image_file,
                file_name="luna_image.jpg",
            ),
            caption=caption,
        )

        return True

    except Exception as error:
        logger.exception(
            "Ошибка отправки фото: %s",
            error,
        )
        return False

    finally:
        image_file.close()

@bot.message_handler(commands=["photo"])
async def handle_photo(message):
    user_id = message.from_user.id

    if not use_photo(user_id):
        await bot.reply_to(
            message,
            (
                "Фото закончились. "
                f"Попробуй через "
                f"{get_time_until_photo_refill(user_id)} "
                "или используй /buy."
            ),
        )
        return

    text = message.text or ""
    text = text.replace(
        "/photo",
        "",
        1,
    ).strip()

    if not text:
        text = "elegant fashion portrait"

    await bot.reply_to(
        message,
        "Генерирую изображение...",
    )

    style = get_style_from_text(text)
    prompt = f"{LUNA_BASE}, {style}"

    success = await send_generated_photo(
        chat_id=message.chat.id,
        prompt=prompt,
        caption="Готово.",
    )

    if not success:
        refund_photo(user_id)

        await bot.reply_to(
            message,
            (
                "Генератор не вернул изображение. "
                "Лимит восстановлен, попробуй ещё раз."
            ),
        )

# ============================================================
# 17. GIF / АНИМАЦИЯ
# ============================================================

async def send_generated_animation(
    chat_id: int,
    gif_file: BytesIO,
    caption: str,
) -> bool:
    try:
        gif_file.seek(0)

        await bot.send_animation(
            chat_id=chat_id,
            animation=InputFile(
                gif_file,
                file_name="luna_animation.gif",
            ),
            caption=caption,
        )

        return True

    except Exception as error:
        logger.exception(
            "Ошибка отправки GIF: %s",
            error,
        )
        return False

    finally:
        gif_file.close()

@bot.message_handler(commands=["gif"])
async def handle_gif_command(message):
    await process_gif_request(message)

@bot.message_handler(
    func=lambda message: bool(
        message.text
        and any(
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
async def handle_gif_text(message):
    await process_gif_request(message)

async def process_gif_request(message):
    user_id = message.from_user.id

    if not MEDIA_AVAILABLE:
        await bot.reply_to(
            message,
            (
                "Медиа отключено. Установи зависимости: "
                "Pillow и aiohttp."
            ),
        )
        return

    if not use_photo(user_id):
        await bot.reply_to(
            message,
            (
                "Лимит медиа исчерпан. "
                f"Попробуй через "
                f"{get_time_until_photo_refill(user_id)} "
                "или используй /buy."
            ),
        )
        return

    await bot.reply_to(
        message,
        "Собираю GIF из кадров...",
    )

    style = get_style_from_text(
        message.text or ""
    )

    prompt_base = f"{LUNA_BASE}, {style}"

    gif_file = await generate_gif(
        prompt_base
    )

    if gif_file is None:
        refund_photo(user_id)

        await bot.reply_to(
            message,
            (
                "Не удалось собрать GIF. "
                "Лимит восстановлен."
            ),
        )
        return

    success = await send_generated_animation(
        chat_id=message.chat.id,
        gif_file=gif_file,
        caption="Готово.",
    )

    if not success:
        refund_photo(user_id)

        await bot.reply_to(
            message,
            (
                "Telegram не принял GIF. "
                "Лимит восстановлен."
            ),
        )

# ============================================================
# 18. КОМАНДА VIDEO
# ============================================================

@bot.message_handler(commands=["video"])
async def handle_video_command(message):
    await bot.reply_to(
        message,
        (
            "В текущей реализации бот создаёт короткую GIF-анимацию "
            "из нескольких кадров. Используй /gif."
        ),
    )

# ============================================================
# 19. АВТОМАТИЧЕСКАЯ ОТПРАВКА ФОТО
# ============================================================

@bot.message_handler(
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
            (
                "Фото закончились. "
                f"Попробуй через "
                f"{get_time_until_photo_refill(user_id)} "
                "или используй /buy."
            ),
        )
        return

    await bot.reply_to(
        message,
        "Делаю изображение...",
    )

    style = get_style_from_text(
        message.text or ""
    )

    prompt = f"{LUNA_BASE}, {style}"

    success = await send_generated_photo(
        chat_id=message.chat.id,
        prompt=prompt,
        caption="Готово.",
    )

    if not success:
        refund_photo(user_id)

        await bot.reply_to(
            message,
            (
                "Не удалось отправить изображение. "
                "Лимит восстановлен."
            ),
        )

# ============================================================
# 20. LLM
# ============================================================

async def generate_luna_reply(
    messages: list,
) -> str:
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
                str(error)[:300],
            )

    return random.choice(
        [
            "Связь немного прервалась. Напиши ещё раз.",
            "Попробуй повторить сообщение.",
        ]
    )

# ============================================================
# 21. ОСНОВНОЙ ЧАТ
# ============================================================

user_last_message = {}
user_message_lock = Lock()

@bot.message_handler(func=lambda message: True)
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

    if any(
        word in lowered_text
        for word in media_words
    ):
        return

    if not use_message(user_id):
        await bot.reply_to(
            message,
            (
                "Лимит сообщений исчерпан. "
                f"Попробуй через "
                f"{get_time_until_msg_refill(user_id)} "
                "или используй /buy."
            ),
        )
        return

    add_to_history(
        user_id,
        "user",
        user_text,
    )

    history = get_user_history(user_id)

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ] + history

    try:
        reply = await generate_luna_reply(
            messages
        )

        add_to_history(
            user_id,
            "assistant",
            reply,
        )

        await bot.reply_to(
            message,
            reply,
        )

        message_count = get_msg_count(
            user_id
        )

        if (
            message_count >= 8
            and random.random() < 0.22
            and use_photo(user_id)
        ):
            await asyncio.sleep(1.2)

            style = get_style_by_stage(
                message_count
            )

            prompt = (
                f"{LUNA_BASE}, {style}"
            )

            success = await send_generated_photo(
                chat_id=message.chat.id,
                prompt=prompt,
                caption="Готово.",
            )

            if not success:
                refund_photo(user_id)

    except Exception as error:
        logger.exception(
            "Ошибка основного обработчика: %s",
            error,
        )

        refund_message(user_id)

        await bot.reply_to(
            message,
            "Что-то пошло не так. Попробуй ещё раз.",
        )

# ============================================================
# 22. ЗАПУСК
# ============================================================

async def main():
    Thread(
        target=run_web,
        daemon=True,
    ).start()

    logger.info(
        "Luna запущена. MEDIA_AVAILABLE=%s",
        MEDIA_AVAILABLE,
    )

    await bot.delete_webhook(
        drop_pending_updates=True,
    )

    await bot.infinity_polling(
        allowed_updates=[
            "message",
            "pre_checkout_query",
        ],
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
