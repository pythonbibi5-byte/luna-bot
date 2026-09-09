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
from telebot.types import LabeledPrice
from openai import AsyncOpenAI

try:
    from PIL import Image, UnidentifiedImageError
    import aiohttp
    GIF_AVAILABLE = True
except ImportError:
    GIF_AVAILABLE = False
    Image = None
    aiohttp = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("LunaEngine")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

ADMIN_KEY = os.getenv("LUNA_ADMIN_KEY", "luna_supreme_777")
ADMIN_HASH = hashlib.sha256(ADMIN_KEY.encode("utf-8")).hexdigest()

if not BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не найден")
    raise SystemExit(1)

bot = AsyncTeleBot(BOT_TOKEN)
app = Flask(__name__)

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

@app.route("/")
def home():
    return "Luna is alive!"

@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": now_utc().isoformat(), "media_available": GIF_AVAILABLE})

def run_web():
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def create_client(base_url: str, api_key: Optional[str]) -> Optional[AsyncOpenAI]:
    if not api_key:
        return None
    return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=45.0, max_retries=2)

groq_client = create_client("https://api.groq.com/openai/v1", GROQ_API_KEY)
openrouter_client = create_client("https://openrouter.ai/api/v1", OPENROUTER_API_KEY)

# Только рабочие провайдеры
MODEL_CHAIN = [
    {
        "name": "Groq",
        "client": groq_client,
        "model": "openai/gpt-oss-20b",
        "max_tokens": 600,
        "temperature": 0.92,
    },
    {
        "name": "OpenRouter",
        "client": openrouter_client,
        "model": "openrouter/free",
        "max_tokens": 600,
        "temperature": 0.9,
    },
]

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

LUNA_BASE = (
    "Luna, 20 years old adult woman, "
    "long dark chestnut hair, green-hazel eyes, "
    "pale smooth skin, large natural breasts, "
    "narrow waist, wide hips, round firm ass"
)

def get_style_from_text(user_text: str) -> str:
    text = user_text.lower().strip()

    if any(word in text for word in ["минет", "сос", "членом в рот", "отсос"]):
        return "explicit blowjob, hard cock in her mouth, oral sex, saliva, detailed face, nsfw, realistic"
    if any(word in text for word in ["анал", "в жоп", "в поп"]):
        return "explicit anal sex, hard cock in her ass, from behind, detailed, nsfw, realistic"
    if any(word in text for word in ["еб", "секс", "трах", "ебл", "конч", "в киск", "в пиз"]):
        return "explicit sex, hard cock penetrating her pussy, missionary, moaning face, detailed genitals, nsfw, realistic"
    if any(word in text for word in ["киск", "пис", "вагин", "дырк"]):
        return "explicit close-up pussy, legs spread, wet, detailed genitals, nsfw, realistic"
    if any(word in text for word in ["груд", "сись", "тить", "между сись"]):
        return "nude, large breasts, titjob, hard cock between tits, detailed nipples, nsfw, realistic"
    if any(word in text for word in ["поп", "жоп", "задниц"]):
        return "nude from behind, round ass, looking back, detailed, nsfw, realistic"
    if any(word in text for word in ["член", "хуй", "голый парень"]):
        return "explicit sex scene with hard cock visible, penetration, detailed, nsfw, realistic"
    if any(word in text for word in ["лиц", "портрет", "глаз"]):
        return "beautiful face portrait, green-hazel eyes, soft lighting, detailed"

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

def build_image_url(prompt: str, width: int, height: int, seed: int) -> str:
    encoded = urllib.parse.quote(prompt[:1200])
    return (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={width}&height={height}&nologo=true&enhance=true"
        f"&model=flux&seed={seed}&safe=false"
    )

async def generate_image(prompt: str) -> str:
    seed = random.randint(1, 9999999)
    extras = random.choice([
        "cinematic lighting, highly detailed skin, 8k",
        "warm intimate light, realistic skin texture",
        "dramatic light, photorealistic, masterpiece",
        "ultra detailed, nsfw, sharp focus",
    ])
    full_prompt = f"{prompt}, {extras}, photorealistic, high quality"
    url = build_image_url(full_prompt, 768, 1024, seed)
    logger.info("Image seed=%s", seed)
    return url

async def check_image_url(url: str) -> bool:
    if not GIF_AVAILABLE:
        return True
    try:
        timeout = aiohttp.ClientTimeout(total=40)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return False
                ct = resp.headers.get("Content-Type", "")
                return ct.startswith("image/")
    except Exception:
        return False

async def download_frame(session, url: str, index: int):
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
            if len(data) < 2000:  # слишком маленькое = скорее всего заглушка
                return None
            img = Image.open(BytesIO(data)).convert("RGB")
            img = img.resize((384, 576), Image.LANCZOS)
            return img
    except Exception:
        return None

async def generate_gif(prompt_base: str):
    if not GIF_AVAILABLE:
        return None

    # Только 4 кадра + последовательная загрузка
    variations = [
        "subtle motion frame 1",
        "slight pose shift frame 2",
        "body movement frame 3",
        "intimate closer view frame 4",
    ]

    frames = []
    timeout = aiohttp.ClientTimeout(total=45)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for i, variation in enumerate(variations):
            seed = random.randint(1, 9999999)
            prompt = f"{prompt_base}, {variation}, photorealistic, nsfw, explicit, 8k"
            url = build_image_url(prompt, 512, 768, seed)

            frame = await download_frame(session, url, i)
            if frame:
                frames.append(frame)
                logger.info("Кадр %s готов", i + 1)
            else:
                logger.warning("Кадр %s пропущен", i + 1)

            await asyncio.sleep(1.1)  # пауза между запросами — критично важно

    if len(frames) < 3:
        logger.error("Мало кадров для GIF: %s", len(frames))
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
    logger.info("GIF готов (%s кадров)", len(frames))
    return buffer

# ---------- Память и лимиты (без изменений) ----------
MAX_HISTORY = 40
user_history = {}
history_lock = Lock()

def get_user_history(user_id: int) -> list:
    with history_lock:
        return list(user_history.get(user_id, []))

def add_to_history(user_id: int, role: str, text: str):
    with history_lock:
        if user_id not in user_history:
            user_history[user_id] = []
        user_history[user_id].append({"role": role, "content": text})
        if len(user_history[user_id]) > MAX_HISTORY:
            user_history[user_id] = user_history[user_id][-MAX_HISTORY:]

def clear_history(user_id: int) -> bool:
    with history_lock:
        existed = user_id in user_history
        user_history[user_id] = []
        return existed

def get_msg_count(user_id: int) -> int:
    return sum(1 for m in get_user_history(user_id) if m.get("role") == "user")

MAX_MESSAGES = 9
MSG_REFILL_INTERVAL = timedelta(minutes=25)
MAX_PHOTOS = 3
PHOTO_REFILL_INTERVAL = timedelta(minutes=45)

user_limit_data = {}
limit_lock = Lock()

def get_limit_data(user_id: int) -> dict:
    with limit_lock:
        if user_id not in user_limit_data:
            now = now_utc()
            user_limit_data[user_id] = {
                "msg_count": MAX_MESSAGES,
                "msg_last_refill": now,
                "photo_count": MAX_PHOTOS,
                "photo_last_refill": now,
                "vip": False,
                "plan": None,
            }
        return user_limit_data[user_id]

def use_message(user_id: int) -> bool:
    data = get_limit_data(user_id)
    with limit_lock:
        if data["vip"]:
            return True
        now = now_utc()
        if now - data["msg_last_refill"] >= MSG_REFILL_INTERVAL:
            data["msg_count"] = MAX_MESSAGES
            data["msg_last_refill"] = now
        if data["msg_count"] <= 0:
            return False
        data["msg_count"] -= 1
        return True

def use_photo(user_id: int) -> bool:
    data = get_limit_data(user_id)
    with limit_lock:
        if data["vip"]:
            return True
        now = now_utc()
        if now - data["photo_last_refill"] >= PHOTO_REFILL_INTERVAL:
            data["photo_count"] = MAX_PHOTOS
            data["photo_last_refill"] = now
        if data["photo_count"] <= 0:
            return False
        data["photo_count"] -= 1
        return True

def get_time_until_msg_refill(user_id: int) -> str:
    data = get_limit_data(user_id)
    with limit_lock:
        if data["vip"]:
            return "безлимит"
        remaining = MSG_REFILL_INTERVAL - (now_utc() - data["msg_last_refill"])
    return "0 мин" if remaining.total_seconds() <= 0 else f"{int(remaining.total_seconds() // 60)} мин"

def get_time_until_photo_refill(user_id: int) -> str:
    data = get_limit_data(user_id)
    with limit_lock:
        if data["vip"]:
            return "безлимит"
        remaining = PHOTO_REFILL_INTERVAL - (now_utc() - data["photo_last_refill"])
    return "0 мин" if remaining.total_seconds() <= 0 else f"{int(remaining.total_seconds() // 60)} мин"

# ---------- Админ и платежи ----------
@bot.message_handler(commands=["admin"])
async def handle_admin(message):
    text = (message.text or "").replace("/admin", "", 1).strip()
    if hashlib.sha256(text.encode()).hexdigest() == ADMIN_HASH:
        data = get_limit_data(message.from_user.id)
        with limit_lock:
            data["vip"] = True
            data["plan"] = "admin"
        await bot.reply_to(message, "Режим БОГА включён. Безлимит.")
    else:
        await bot.reply_to(message, "Неверный ключ.")

PLANS = {
    "plus": {"title": "Luna Plus", "description": "Безлимитные сообщения и фото", "price": 500, "payload": "luna_plus"},
    "vip": {"title": "Luna VIP", "description": "Безлимит и дополнительные возможности", "price": 1500, "payload": "luna_vip"},
}

async def send_stars_invoice(user_id: int, plan: str = "plus"):
    p = PLANS.get(plan, PLANS["plus"])
    try:
        await bot.send_invoice(
            chat_id=user_id,
            title=p["title"],
            description=p["description"],
            invoice_payload=p["payload"],
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=p["title"], amount=p["price"])],
            start_parameter=f"luna_buy_{plan}",
        )
    except Exception as e:
        logger.exception(e)
        await bot.send_message(user_id, "Не удалось выставить счёт.")

@bot.message_handler(commands=["start"])
async def handle_start(message):
    user_id = message.from_user.id
    text = (message.text or "").lower()
    if "buy_plus" in text:
        await send_stars_invoice(user_id, "plus")
        return
    if "buy_vip" in text:
        await send_stars_invoice(user_id, "vip")
        return
    clear_history(user_id)
    await bot.reply_to(message, "Привет, детка… Я Луна. Напиши мне что-нибудь.")

@bot.message_handler(commands=["buy"])
async def handle_buy(message):
    plan = "vip" if "vip" in (message.text or "").lower() else "plus"
    await send_stars_invoice(message.from_user.id, plan)

@bot.pre_checkout_query_handler(func=lambda q: True)
async def handle_pre_checkout(query):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@bot.message_handler(content_types=["successful_payment"])
async def handle_successful_payment(message):
    payload = message.successful_payment.invoice_payload
    plan = "vip" if payload == "luna_vip" else "plus"
    data = get_limit_data(message.from_user.id)
    with limit_lock:
        data["vip"] = True
        data["plan"] = plan
    await bot.reply_to(message, f"Подписка {plan.upper()} активна.")

@bot.message_handler(commands=["clear"])
async def handle_clear(message):
    await bot.reply_to(message, "История очищена." if clear_history(message.from_user.id) else "История и так пустая.")

async def send_generated_photo(chat_id: int, prompt: str, caption: str) -> bool:
    url = await generate_image(prompt)
    if not await check_image_url(url):
        return False
    try:
        await bot.send_photo(chat_id, photo=url, caption=caption)
        return True
    except Exception as e:
        logger.exception(e)
        return False

@bot.message_handler(commands=["photo"])
async def handle_photo(message):
    user_id = message.from_user.id
    if not use_photo(user_id):
        await bot.reply_to(message, f"Фото закончились. Через {get_time_until_photo_refill(user_id)}.\n/buy")
        return
    text = (message.text or "").replace("/photo", "", 1).strip() or "sensual nude"
    await bot.reply_to(message, "Делаю...")
    style = get_style_from_text(text)
    success = await send_generated_photo(message.chat.id, f"{LUNA_BASE}, {style}", "Для тебя.")
    if not success:
        await bot.reply_to(message, "Не удалось получить изображение.")

@bot.message_handler(content_types=["text"], func=lambda m: bool(m.text and any(w in m.text.lower() for w in ["видео", "гиф", "gif", "анимац"])))
async def handle_gif(message):
    user_id = message.from_user.id
    if not use_photo(user_id):
        await bot.reply_to(message, f"Лимит медиа. Через {get_time_until_photo_refill(user_id)}.\n/buy")
        return
    if not GIF_AVAILABLE:
        await bot.reply_to(message, "GIF отключён.")
        return

    await bot.reply_to(message, "Делаю мини-видео...")

    style = get_style_from_text(message.text)
    if style.startswith("full body"):
        style = get_style_by_stage(get_msg_count(user_id))

    prompt_base = f"{LUNA_BASE}, {style}"
    gif_data = await generate_gif(prompt_base)

    if not gif_data:
        # Если GIF не вышел — сразу кидаем фото
        await bot.reply_to(message, "GIF не собрался, кидаю фото...")
        await send_generated_photo(message.chat.id, prompt_base, "Вот так.")
        return

    try:
        gif_data.seek(0)
        gif_data.name = "luna.gif"
        await bot.send_animation(message.chat.id, animation=gif_data, caption="Мини-видео для тебя.")
    except Exception as e:
        logger.exception(e)
        await bot.reply_to(message, "Не смогла отправить GIF.")

@bot.message_handler(content_types=["text"], func=lambda m: bool(m.text and any(w in m.text.lower() for w in ["скинь", "покажи", "фото"]) and not any(w in m.text.lower() for w in ["видео", "гиф", "gif", "анимац"])))
async def auto_photo(message):
    user_id = message.from_user.id
    if not use_photo(user_id):
        await bot.reply_to(message, f"Фото закончились. Через {get_time_until_photo_refill(user_id)}.\n/buy")
        return
    await bot.reply_to(message, "Держи...")
    style = get_style_from_text(message.text)
    success = await send_generated_photo(message.chat.id, f"{LUNA_BASE}, {style}", "Специально для тебя.")
    if not success:
        await bot.reply_to(message, "Не удалось отправить.")

async def generate_luna_reply(messages: list) -> str:
    for provider in MODEL_CHAIN:
        client = provider["client"]
        if not client:
            continue
        try:
            resp = await client.chat.completions.create(
                model=provider["model"],
                messages=messages,
                max_tokens=provider["max_tokens"],
                temperature=provider["temperature"],
            )
            content = resp.choices[0].message.content if resp.choices else None
            if content and len(content.strip()) > 2:
                logger.info("Ответ от %s", provider["name"])
                return content.strip()
        except Exception as e:
            logger.warning("%s: %s", provider["name"], str(e)[:150])
    return random.choice([
        "Малыш, связь чуть пропала… напиши ещё раз.",
        "Бля, мысли унеслись… повтори, сладкий.",
    ])

user_last_message = {}
user_message_lock = Lock()

@bot.message_handler(content_types=["text"], func=lambda m: True)
async def handle_message(message):
    if not message.text:
        return
    user_id = message.from_user.id
    user_text = message.text.strip()
    if not user_text or user_text.startswith("/"):
        return

    now = time.monotonic()
    with user_message_lock:
        if user_id in user_last_message and now - user_last_message[user_id] < 0.6:
            return
        user_last_message[user_id] = now

    if any(w in user_text.lower() for w in ["скинь", "покажи", "фото", "видео", "гиф", "gif", "анимац"]):
        return

    if not use_message(user_id):
        await bot.reply_to(message, f"Лимит сообщений. Через {get_time_until_msg_refill(user_id)}.\n/buy")
        return

    add_to_history(user_id, "user", user_text)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + get_user_history(user_id)

    try:
        reply = await generate_luna_reply(messages)
        add_to_history(user_id, "assistant", reply)
        await bot.reply_to(message, reply)

        if get_msg_count(user_id) >= 8 and random.random() < 0.22 and use_photo(user_id):
            await asyncio.sleep(1.0)
            style = get_style_by_stage(get_msg_count(user_id))
            caption = random.choice(["Вот… смотри на меня.", "Завелась и скинула тебе.", "Только тебе."])
            await send_generated_photo(message.chat.id, f"{LUNA_BASE}, {style}", caption)
    except Exception as e:
        logger.exception(e)
        await bot.reply_to(message, "Что-то пошло не так… напиши ещё раз.")

async def main():
    Thread(target=run_web, daemon=True).start()
    logger.info("Luna запущена")
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.infinity_polling(allowed_updates=["message", "callback_query", "pre_checkout_query"])

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
