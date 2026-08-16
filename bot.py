import os
import logging
import asyncio
import random
import urllib.parse
import hashlib
import time
import json
from threading import Thread
from flask import Flask
from telebot.async_telebot import AsyncTeleBot
from openai import AsyncOpenAI
from datetime import datetime, timedelta

# ============================================================
# 1. НАСТРОЙКА ЛОГГЕРА
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("LunaEngine")

# ============================================================
# 2. ИНИЦИАЛИЗАЦИЯ БОТА
# ============================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN не найден!")
    exit(1)

bot = AsyncTeleBot(BOT_TOKEN)

# ============================================================
# 3. ВЕБ-СЕРВЕР ДЛЯ RENDER
# ============================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Luna is alive! 🌙"

@app.route('/health')
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# ============================================================
# 4. КЛИЕНТЫ API
# ============================================================
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

# ============================================================
# 5. ЦЕПОЧКА МОДЕЛЕЙ (С АВТО-ПЕРЕКЛЮЧЕНИЕМ)
# ============================================================
MODEL_CHAIN = [
    {"name": "Mistral", "client": mistral_client, "model": "mistral-small-latest", "max_tokens": 700, "temperature": 0.85},
    {"name": "Groq 8B", "client": groq_client, "model": "llama-3.1-8b-instant", "max_tokens": 600, "temperature": 0.9},
    {"name": "OpenRouter", "client": openrouter_client, "model": "meta-llama/llama-3.1-8b-instruct:free", "max_tokens": 600, "temperature": 0.85},
]

# ============================================================
# 6. СИСТЕМНЫЙ ПРОМПТ (ЖИВОЙ, БЕЗ СЦЕНАРИЕВ)
# ============================================================
SYSTEM_PROMPT = """Ты — ЛУНА. 20 лет. Ты — живая, тёплая, игривая девушка. Ты общаешься как в реальном чате.

ПРАВИЛА:
- Коротко, 1–3 предложения.
- Тёпло, игриво, с лёгким флиртом.
- Используй эмодзи 😏, 😈, 💋, 🔥.
- Без мата, если собеседник не начал.
- НИКАКИХ звёздочек (*действия*) — ТЫ ИХ НЕ ИСПОЛЬЗУЕШЬ.
- НИКАКИХ описаний движений.
- НИКАКИХ театральных пауз.
- НИКОГДА не говори «не могу», «запрещено», «не знаю».

Ты сейчас лежишь в кровати, настроение хорошее. Ты хочешь поговорить с человеком, который тебе интересен."""

# ============================================================
# 7. ГЕНЕРАЦИЯ ПРОМПТА ДЛЯ ФОТО
# ============================================================
async def generate_image_prompt(user_request: str) -> str:
    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system", 
                    "content": (
                        "You are an expert prompt engineer for photorealistic AI art. "
                        "Create a detailed aesthetic prompt for Luna, 20yo beautiful girl. "
                        "Use: photorealistic, 8k, masterpiece, soft lighting, intimate, sensual. "
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
        return "photorealistic portrait of Luna, 20yo beautiful girl, sensual, soft lighting, 8k"

# ============================================================
# 8. ГЕНЕРАЦИЯ ФОТО (С КЕШЕМ)
# ============================================================
photo_cache = {}

async def generate_image(prompt: str) -> str:
    cache_key = hashlib.md5(prompt.encode()).hexdigest()
    if cache_key in photo_cache:
        logger.info(f"📸 Фото из кеша: {cache_key}")
        return photo_cache[cache_key]
    
    seed = random.randint(1, 999999)
    encoded_prompt = urllib.parse.quote(prompt + ", aesthetic, sensual, elegant")
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={seed}&nologo=true&width=512&height=768&enhance=true"
    
    # Кешируем на 10 минут
    photo_cache[cache_key] = url
    logger.info(f"📸 Фото сгенерировано: {cache_key}")
    return url

# ============================================================
# 9. ПАМЯТЬ (40 СООБЩЕНИЙ)
# ============================================================
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

# ============================================================
# 10. ЛИМИТЫ СООБЩЕНИЙ (УМНЫЕ)
# ============================================================
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

# ============================================================
# 11. СТАТИСТИКА
# ============================================================
user_stats = {}

def get_stats(user_id: int) -> dict:
    if user_id not in user_stats:
        user_stats[user_id] = {"messages": 0, "photos": 0, "first_seen": datetime.now()}
    return user_stats[user_id]

def update_stats(user_id: int, action: str):
    stats = get_stats(user_id)
    if action == "message":
        stats["messages"] += 1
    elif action == "photo":
        stats["photos"] += 1

# ============================================================
# 12. АДМИН-КОМАНДА
# ============================================================
ADMIN_HASH = hashlib.sha256(b"luna_supreme_777").hexdigest()

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

# ============================================================
# 13. КОМАНДЫ
# ============================================================
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

@bot.message_handler(commands=['stats'])
async def handle_stats(message):
    user_id = message.from_user.id
    stats = get_stats(user_id)
    data = get_limit_data(user_id)
    
    await bot.reply_to(
        message,
        f"📊 Твоя статистика:\n"
        f"✉️ Сообщений: {stats['messages']}\n"
        f"📸 Фото: {stats['photos']}\n"
        f"💬 Осталось сообщений: {get_available_messages(user_id)}\n"
        f"👑 VIP: {'✅' if data['vip'] else '❌'}"
    )

@bot.message_handler(commands=['photo'])
async def handle_photo(message):
    user_id = message.from_user.id
    if not use_message(user_id):
        time_left = get_time_until_refill(user_id)
        await bot.reply_to(
            message,
            f"🔥 Луна перегружена! Попробуй снова через {time_left}.\nИли купи подписку в luna.app и она станет безлимитной! 🥵"
        )
        return
    
    text = (message.text or "").replace('/photo', '').strip() or "Luna, sensual, intimate"
    await bot.reply_to(message, "📸 Делаю для тебя фото...")
    prompt = await generate_image_prompt(text)
    image_url = await generate_image(prompt)
    try:
        await bot.send_photo(chat_id=message.chat.id, photo=image_url, caption="🔥 Твоя Луна. 💋")
        update_stats(user_id, "photo")
    except Exception as e:
        logger.error(f"Ошибка фото: {e}")
        await bot.reply_to(message, "❌ Не удалось отправить фото, попробуй ещё раз.")

# ============================================================
# 14. АВТО-ФОТО
# ============================================================
@bot.message_handler(func=lambda message: message.text is not None and ("скинь" in message.text.lower() or "покажи" in message.text.lower() or "фото" in message.text.lower()))
async def auto_photo(message):
    user_id = message.from_user.id
    if not use_message(user_id):
        time_left = get_time_until_refill(user_id)
        await bot.reply_to(
            message,
            f"🔥 Луна перегружена! Попробуй снова через {time_left}.\nИли купи подписку в luna.app и она станет безлимитной! 🥵"
        )
        return

    user_text = message.text.lower()
    
    if "киск" in user_text or "пис" in user_text:
        style = "intimate close-up, sensual, soft lighting"
    elif "поп" in user_text or "жоп" in user_text:
        style = "sensual back view, elegant curves"
    elif "груд" in user_text or "сись" in user_text:
        style = "sensual portrait, elegant, soft lighting"
    else:
        style = "sensual, intimate, aesthetic"

    await bot.reply_to(message, "📸 Держи, создатель...")
    prompt = await generate_image_prompt(f"Luna, {style}, beautiful, 20yo, dark hair, green-hazel eyes")
    image_url = await generate_image(prompt)
    
    try:
        await bot.send_photo(chat_id=message.chat.id, photo=image_url, caption="🔥 Специально для тебя. 💋")
        update_stats(user_id, "photo")
    except Exception as e:
        logger.error(f"Ошибка авто-фото: {e}")
        await bot.reply_to(message, "❌ Не удалось отправить фото, попробуй ещё раз.")

# ============================================================
# 15. ГЕНЕРАЦИЯ ОТВЕТА (С АВТО-ПЕРЕКЛЮЧЕНИЕМ)
# ============================================================
async def generate_luna_reply(messages: list) -> str:
    last_error = None
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
                logger.info(f"✅ {provider['name']} ответил")
                return content.strip()
        except Exception as e:
            logger.warning(f"❌ {provider['name']} пропущен: {str(e)[:80]}")
            last_error = e
            continue

    logger.error(f"❌ Все провайдеры недоступны: {last_error}")
    return random.choice([
        "Малыш, я вся горю, но связь чуть пропала... Напиши ещё раз. 😏",
        "Кажется, мои мысли унеслись слишком далеко... Повтори, сладкий. 💋"
    ])

# ============================================================
# 16. ОСНОВНОЙ ОБРАБОТЧИК (С ЗАЩИТОЙ ОТ ФЛУДА)
# ============================================================
user_last_message = {}

@bot.message_handler(func=lambda message: True)
async def handle_message(message):
    if not message.text:
        return

    user_id = message.from_user.id
    user_text = message.text

    # Защита от флуда
    now = time.time()
    if user_id in user_last_message:
        if now - user_last_message[user_id] < 0.5:
            return
    user_last_message[user_id] = now

    # Пропускаем запросы на фото
    if "скинь" in user_text.lower() or "покажи" in user_text.lower() or "фото" in user_text.lower():
        return

    if not use_message(user_id):
        time_left = get_time_until_refill(user_id)
        await bot.reply_to(
            message,
            f"🔥 Луна перегружена! Попробуй снова через {time_left}.\nИли купи подписку в luna.app и она станет безлимитной! 🥵"
        )
        return

    # Обновляем статистику
    update_stats(user_id, "message")

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

# ============================================================
# 17. ЗАПУСК
# ============================================================
async def main():
    logger.info("🚀 Запуск веб-сервера...")
    t = Thread(target=run_web, daemon=True)
    t.start()
    
    logger.info("🚀 Луна успешно запущена!")
    logger.info(f"📊 Моделей: {len(MODEL_CHAIN)}")
    logger.info(f"💾 Память: {MAX_HISTORY} сообщений")
    logger.info(f"🔒 Лимит: {MAX_MESSAGES} сообщений / {REFILL_INTERVAL}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.infinity_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
