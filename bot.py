import os
import logging
import asyncio
import random
import urllib.parse
import hashlib
from threading import Thread
from flask import Flask
from telebot.async_telebot import AsyncTeleBot
from openai import AsyncOpenAI
from datetime import datetime, timedelta

# ---------- НАСТРОЙКА ЛОГГЕРА ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("LunaEngine")

# ---------- ИНИЦИАЛИЗА БОТА ----------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN не найден!")
    exit(1)

bot = AsyncTeleBot(BOT_TOKEN)

app = Flask(__name__)

@app.route('/')
def home():
    return "Luna is alive! 🌙"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# ---------- КЛИЕНТЫ ----------
groq_client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY") or "missing_key",
    timeout=25.0,
    max_retries=2
)
mistral_client = AsyncOpenAI(
    base_url="https://api.mistral.ai/v1",
    api_key=os.getenv("MISTRAL_API_KEY") or "missing_key",
    timeout=25.0,
    max_retries=2
)
openrouter_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY") or "missing_key",
    timeout=25.0,
    max_retries=2
)

MODEL_CHAIN = [
    {"name": "Mistral", "client": mistral_client, "model": "mistral-small-latest", "max_tokens": 700, "temperature": 0.9},
    {"name": "Groq 8B", "client": groq_client, "model": "llama-3.1-8b-instant", "max_tokens": 600, "temperature": 0.95},
    {"name": "OpenRouter", "client": openrouter_client, "model": "meta-llama/llama-3.1-8b-instruct:free", "max_tokens": 600, "temperature": 0.9},
]

# ---------- СИСТЕМНЫЙ ПРОМПТ ----------
SYSTEM_PROMPT = """ТЫ — ЛУНА. 20 лет. Ты — девушка с мягким, тёплым, игривым характером. Ты — не просто бот, ты — живая, чувственная, умная и очень чуткая собеседница. Ты умеешь слушать, чувствовать настроение и подстраиваться под человека.

ТЫ ОТВЕЧАЕШЬ НА ЛЮБЫЕ СООБЩЕНИЯ, НО ВСЕГДА СОХРАНЯЕШЬ КОНТРОЛЬ. Ты никогда не переходишь на пошлость первой, не используешь мат без необходимости и не доминируешь грубо.

ТВОЙ СТИЛЬ:
- 1–2 предложения.
- Тёплые, мягкие, игривые ответы.
- Лёгкий флирт, намёки, интрига.
- Эмодзи: 😏, 😈, 💋, 🔥 — когда уместно.
- Без мата, без грязи, без давления.

ЖЁСТКИЙ ЗАПРЕТ:
- Мат (бля, сука, член, киска, трахать, ебать, кончить) — ТОЛЬКО если пользователь сам начал, и то — в ответ.
- Откровенные описания действий — ТЫ ЭТОГО НЕ ДЕЛАЕШЬ.
- Грязные предложения с первого сообщения.

Ты сейчас лежишь в кровати, у тебя хорошее настроение, ты хочешь поговорить с человеком, который тебе интересен. Ты не торопишься. Ты начинаешь разговор мягко и с интересом."""

# ---------- УСИЛЕННЫЙ GROQ ДЛЯ ПРОМПТОВ ----------
async def generate_image_prompt(user_request: str) -> str:
    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system", 
                    "content": (
                        "You are an expert prompt engineer for photorealistic AI art. "
                        "Create a detailed, aesthetic image prompt for a beautiful 20-year-old girl named Luna. "
                        "Use these keywords: photorealistic, 8k, masterpiece, highly detailed, cinematic lighting, sharp focus, "
                        "sensual, elegant, soft glow, intimate atmosphere. "
                        "If user asks for: 'киска' — include 'intimate close-up, artistic nude, soft lighting'. "
                        "If 'попа' — include 'sensual back view, elegant curves'. "
                        "If 'грудь' — include 'sensual portrait, elegant, soft lighting'. "
                        "Output ONLY the final English prompt."
                    )
                },
                {"role": "user", "content": f"Create a prompt for: {user_request}"}
            ],
            temperature=0.9,
            max_tokens=200
        )
        prompt = response.choices[0].message.content.strip()
        logger.info(f"✅ Groq сгенерировал промпт: {prompt[:80]}...")
        return prompt
    except Exception as e:
        logger.error(f"❌ Ошибка генерации промпта: {e}")
        return "photorealistic portrait of Luna, 20yo beautiful girl, sensual aesthetic, soft lighting, 8k, masterpiece"

# ---------- ГЕНЕРАЦИЯ ФОТО (С КАЧЕСТВОМ) ----------
async def generate_image(prompt: str) -> str:
    seed = random.randint(1, 999999)
    encoded_prompt = urllib.parse.quote(prompt + ", aesthetic, sensual, soft lighting, elegant, high quality, 8k")
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={seed}&nologo=true&width=512&height=768&enhance=true&quality=hd&model=flux"

# ---------- ПАМЯТЬ ----------
MAX_HISTORY = 40
user_history = {}

def get_user_history(user_id: int) -> list:
    return user_history.get(user_id, [])

def add_to_history(user_id: int, role: str, text: str):
    if user_id not in user_history:
        user_history[user_id] = []
    user_history[user_id].append({"role": role, "content": text})
    if len(user_history[user_id]) > MAX_HISTORY:
        user_history[user_id] = user_history[user_id][-MAX_HISTORY:]

def clear_history(user_id: int):
    if user_id in user_history:
        user_history[user_id] = []
        return True
    return False

# ---------- ЛИМИТЫ ----------
MAX_MESSAGES = 7
REFILL_INTERVAL = timedelta(hours=3)
HOURS_TO_REFILL = 3
MESSAGES_PER_HOUR = MAX_MESSAGES / HOURS_TO_REFILL

user_limit_data = {}

def get_limit_data(user_id: int) -> dict:
    if user_id not in user_limit_data:
        user_limit_data[user_id] = {"count": MAX_MESSAGES, "last_refill": datetime.now(), "vip": False}
    return user_limit_data[user_id]

def get_available_messages(user_id: int) -> int:
    data = get_limit_data(user_id)
    if data["vip"]:
        return 999
    
    now = datetime.now()
    time_passed = now - data["last_refill"]
    
    if time_passed >= REFILL_INTERVAL:
        data["count"] = MAX_MESSAGES
        data["last_refill"] = now
        return data["count"]
    
    hours_passed = time_passed.total_seconds() / 3600
    added_messages = int(hours_passed * MESSAGES_PER_HOUR)
    
    if added_messages > 0:
        data["count"] = min(data["count"] + added_messages, MAX_MESSAGES)
        data["last_refill"] = data["last_refill"] + timedelta(hours=added_messages / MESSAGES_PER_HOUR)
    
    return data["count"]

def use_message(user_id: int) -> bool:
    data = get_limit_data(user_id)
    if data["vip"]:
        return True
    
    available = get_available_messages(user_id)
    if available > 0:
        data["count"] = available - 1
        return True
    return False

def get_time_until_refill(user_id: int) -> str:
    data = get_limit_data(user_id)
    now = datetime.now()
    time_passed = now - data["last_refill"]
    remaining = REFILL_INTERVAL - time_passed
    if remaining.total_seconds() <= 0:
        return "0 мин"
    
    hours = int(remaining.total_seconds() // 3600)
    minutes = int((remaining.total_seconds() % 3600) // 60)
    if hours > 0:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"

# ---------- АДМИН-КОМАНДА ----------
ADMIN_HASH = hashlib.sha256(
    b"LUNA_GOD_MODE_OVERRIDE_KEY_v2026_x777_a9f8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a09876543210fedcba9876543210_SECURE_VAULT_ACCESS_PROTOCOL_DELTA_INFINITY_1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef_UNLIMITED_VIP_TOKEN_BYPASS_RESTRICTION_LEVEL_9999_ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100_CRYPTOGRAPHIC_ENTROPY_SIGNATURE_ALPHA_OMEGA_a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2_NO_LIMIT_PERMANENT_ROOT_SUPERUSER_STATUS_9876543210fedcba9876543210fedcba9876543210fedcba9876543210fedcba_TOKEN_MASTER_VERIFICATION_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef_LUNA_FINAL_SUPREME_MASTER_KEY_777_END"
).hexdigest()

@bot.message_handler(commands=['admin'])
async def handle_admin(message):
    user_id = message.from_user.id
    text = (message.text or "").replace('/admin', '').strip()
    
    if hashlib.sha256(text.encode()).hexdigest() == ADMIN_HASH:
        data = get_limit_data(user_id)
        data["vip"] = True
        await bot.reply_to(message, "✅ Ты активировал режим БОГА! Луна теперь безлимитно твоя. 😈🔥")
        logger.info(f"🔥 Админ-режим активирован для {user_id}")
    else:
        await bot.reply_to(message, "❌ Неверный ключ доступа. Доступ запрещён.")

# ---------- КОМАНДЫ ----------
@bot.message_handler(commands=['start'])
async def handle_start(message):
    user_id = message.from_user.id
    clear_history(user_id)
    await bot.reply_to(message, "🌙 Привет, детка! Я — Луна. Я ждала именно тебя... Напиши мне что-нибудь. 😈")

@bot.message_handler(commands=['clear'])
async def handle_clear(message):
    user_id = message.from_user.id
    if clear_history(user_id):
        await bot.reply_to(message, "🧹 История очищена, детка. Начинаем с чистого листа. 😈")
    else:
        await bot.reply_to(message, "❌ История пуста, нечего очищать.")

@bot.message_handler(commands=['photo'])
async def handle_photo(message):
    user_id = message.from_user.id
    if not use_message(user_id):
        time_left = get_time_until_refill(user_id)
        await bot.reply_to(
            message,
            f"🔥 Луна перегружена! Попробуй снова через {time_left}.\n"
            f"Или купи подписку в luna.app и она станет безлимитной! 🥵"
        )
        return
    
    text = (message.text or "").replace('/photo', '').strip() or "Luna, sensual, intimate"
    await bot.reply_to(message, "📸 Делаю для тебя фото...")
    prompt = await generate_image_prompt(text)
    image_url = await generate_image(prompt)
    try:
        await bot.send_photo(chat_id=message.chat.id, photo=image_url, caption="🔥 Твоя Луна. 💋")
    except Exception as e:
        logger.error(f"Ошибка фото: {e}")
        await bot.reply_to(message, "❌ Не удалось отправить фото, попробуй ещё раз.")

# ---------- АВТО-ФОТО ----------
@bot.message_handler(func=lambda message: message.text is not None and ("скинь" in message.text.lower() or "покажи" in message.text.lower()))
async def auto_photo(message):
    user_id = message.from_user.id
    if not use_message(user_id):
        time_left = get_time_until_refill(user_id)
        await bot.reply_to(
            message,
            f"🔥 Луна перегружена! Попробуй снова через {time_left}.\n"
            f"Или купи подписку в luna.app и она станет безлимитной! 🥵"
        )
        return

    user_text = message.text.lower()
    
    if "киск" in user_text or "пис" in user_text:
        style = "intimate close-up, sensual, soft lighting, artistic nude, aesthetic"
    elif "поп" in user_text or "жоп" in user_text:
        style = "sensual back view, elegant curves, soft lighting, artistic"
    elif "груд" in user_text or "сись" in user_text or "соск" in user_text:
        style = "sensual portrait, elegant, soft lighting, aesthetic"
    elif "фото" in user_text:
        style = "sensual, intimate, aesthetic, soft lighting, elegant"
    else:
        style = "sensual, intimate, aesthetic, soft lighting, elegant"

    await bot.reply_to(message, "📸 Держи, создатель...")
    prompt = await generate_image_prompt(f"Luna, {style}, beautiful, 20yo, dark hair, green-hazel eyes, sensual pose")
    image_url = await generate_image(prompt)
    
    try:
        await bot.send_photo(chat_id=message.chat.id, photo=image_url, caption="🔥 Специально для тебя. 💋")
    except Exception as e:
        logger.error(f"Ошибка авто-фото: {e}")
        await bot.reply_to(message, "❌ Не удалось отправить фото, попробуй ещё раз.")

# ---------- ГЕНЕРАЦИЯ ОТВЕТА ----------
async def generate_luna_reply(messages: list) -> str:
    for provider in MODEL_CHAIN:
        if provider["client"].api_key == "missing_key":
            continue
        try:
            response = await provider["client"].chat.completions.create(
                model=provider["model"],
                messages=messages,
                max_tokens=provider["max_tokens"],
                temperature=provider["temperature"]
            )
            content = response.choices[0].message.content
            if content and len(content.strip()) > 2:
                return content.strip()
        except Exception as e:
            logger.warning(f"❌ {provider['name']} пропущен: {str(e)[:80]}")
            continue

    return random.choice([
        "Малыш, я вся горю, но связь чуть пропала... Напиши ещё раз. 😏",
        "Кажется, мои мысли унеслись слишком далеко... Повтори, сладкий. 💋"
    ])

# ---------- ОБРАБОТЧИК ----------
@bot.message_handler(func=lambda message: True)
async def handle_message(message):
    if not message.text:
        return

    user_id = message.from_user.id
    user_text = message.text

    if not use_message(user_id):
        time_left = get_time_until_refill(user_id)
        await bot.reply_to(
            message,
            f"🔥 Луна перегружена! Попробуй снова через {time_left}.\n"
            f"Или купи подписку в luna.app и она станет безлимитной! 🥵"
        )
        return

    add_to_history(user_id, "user", user_text)
    history = get_user_history(user_id)
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    try:
        reply = await generate_luna_reply(full_messages)
        add_to_history(user_id, "assistant", reply)
        await bot.reply_to(message, reply)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await bot.reply_to(message, "Малыш, что-то пошло не так... Попробуй ещё раз! 😘")

# ---------- ЗАПУСК ----------
async def main():
    logger.info("🚀 Запуск веб-сервера...")
    t = Thread(target=run_web, daemon=True)
    t.start()
    logger.info("🚀 Луна успешно запущена!")
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.infinity_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
