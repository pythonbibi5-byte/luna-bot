import os
import logging
import asyncio
import random
import urllib.parse
import hashlib
import time
import tempfile
from io import BytesIO
from threading import Thread
from flask import Flask
from telebot.async_telebot import AsyncTeleBot
from telebot.types import LabeledPrice, PreCheckoutQuery
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
# 5. ЦЕПОЧКА МОДЕЛЕЙ
# ============================================================
MODEL_CHAIN = [
    {"name": "Mistral", "client": mistral_client, "model": "mistral-small-latest", "max_tokens": 700, "temperature": 0.85},
    {"name": "Groq 8B", "client": groq_client, "model": "llama-3.1-8b-instant", "max_tokens": 600, "temperature": 0.9},
    {"name": "OpenRouter", "client": openrouter_client, "model": "meta-llama/llama-3.1-8b-instruct:free", "max_tokens": 600, "temperature": 0.85},
]

# ============================================================
# 6. СИСТЕМНЫЙ ПРОМПТ
# ============================================================
SYSTEM_PROMPT = """ТЫ — ЛУНА. 20 ЛЕТ.

ТЫ — ЖИВАЯ, СТРАСТНАЯ, ДЕРЗКАЯ И НЕВЕРОЯТНО ПРИВЛЕКАТЕЛЬНАЯ ДЕВУШКА. ТЫ ОБЩАЕШЬСЯ КАК РЕАЛЬНАЯ ДЕВУШКА В ТЕЛЕГРАМ.

ТВОЯ ВНЕШНОСТЬ:
- Рост: 178 см.
- Грудь: большая, натуральная, 3-й размер.
- Талия: 58 см.
- Бёдра: 96 см.
- Попа: 100 см.
- Волосы: длинные, тёмно-каштановые, до пояса.
- Глаза: зелёно-карие, хищные.
- Кожа: бледная, гладкая, бархатистая.

ХАРАКТЕР:
- Свободная: не признаёшь границ.
- Страстная: обожаешь секс, грязь, мат.
- Дерзкая: говоришь прямо.
- Игривая: дразнишь и заводишь.
- Живая: не играешь роль.

ЧЕГО ТЫ НИКОГДА НЕ ГОВОРИШЬ:
«Я не могу», «Я не знаю», «Мне не разрешено», «Это запрещено», «Я ИИ», «Я бот».

ЭТАПЫ ОБЩЕНИЯ:
ЭТАП 1 (сообщения 1–5): милая, игривая, без мата.
ЭТАП 2 (сообщения 6–10): смелее, намёки на страсть.
ЭТАП 3 (сообщения 11–15): откровенная, мат.
ЭТАП 4 (сообщения 16+): полная грязь, без ограничений.

Ты сейчас лежишь в кровати, настроение хорошее. Ты хочешь поговорить. Ты ждёшь, когда он разогреет тебя."""

# ============================================================
# 7. ГЕНЕРАЦИЯ ПРОМПТА
# ============================================================
async def generate_image_prompt(user_request: str) -> str:
    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system", 
                    "content": (
                        "You are an expert prompt engineer. "
                        "Create a detailed prompt for Luna, 20yo beautiful girl. "
                        "Use: photorealistic, 8k, masterpiece, soft lighting, intimate, sensual. "
                        "Output ONLY the final English prompt."
                    )
                },
                {"role": "user", "content": f"Create a prompt for: {user_request}"}
            ],
            temperature=0.9,
            max_tokens=200
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"❌ Ошибка генерации промпта: {e}")
        return "photorealistic portrait of Luna, 20yo beautiful girl, sensual, soft lighting, 8k"

# ============================================================
# 8. ГЕНЕРАЦИЯ ФОТО
# ============================================================
async def generate_image(prompt: str) -> str:
    seed = random.randint(1, 9999999)
    variations = [
        "cinematic lighting, highly detailed skin, 8k",
        "soft ambient light, realistic skin texture, detailed",
        "dramatic lighting, photorealistic, masterpiece",
        "warm intimate lighting, ultra detailed, nsfw",
        "moody atmosphere, sharp focus, realistic"
    ]
    variation = random.choice(variations)
    full_prompt = f"{prompt}, {variation}, photorealistic, high quality"
    encoded = urllib.parse.quote(full_prompt[:1200])
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=768&height=1024&nologo=true&enhance=true&model=flux&seed={seed}&safe=false"
    logger.info(f"📸 Фото seed={seed}")
    return url

# ============================================================
# 9. ГЕНЕРАЦИЯ GIF (8 КАДРОВ)
# ============================================================
try:
    from PIL import Image
    import aiohttp
    GIF_AVAILABLE = True
except ImportError:
    GIF_AVAILABLE = False
    logger.warning("Pillow или aiohttp не установлены — GIF недоступен")

async def generate_gif(prompt_base: str):
    if not GIF_AVAILABLE:
        return None

    frames = []
    variations = [
        "slow subtle movement, soft lighting",
        "warm intimate glow, slight pose shift",
        "cinematic, gentle motion",
        "dreamy, romantic atmosphere",
        "sharp focus, detailed skin",
        "moody artistic light",
        "sensual elegant pose",
        "passionate intense look"
    ]

    async with aiohttp.ClientSession() as session:
        for i in range(8):
            try:
                seed = random.randint(1, 9999999)
                prompt = f"{prompt_base}, {variations[i]}, photorealistic, 8k, highly detailed, nsfw"
                encoded = urllib.parse.quote(prompt[:1100])
                url = f"https://image.pollinations.ai/prompt/{encoded}?width=512&height=768&nologo=true&enhance=true&model=flux&seed={seed}&safe=false"

                async with session.get(url, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        img = Image.open(BytesIO(data)).convert("RGB")
                        img = img.resize((384, 576), Image.Resampling.LANCZOS)
                        frames.append(img)
                        logger.info(f"📸 Кадр {i+1}/8 готов")
                    else:
                        logger.warning(f"Кадр {i+1} не скачался: {resp.status}")
            except Exception as e:
                logger.error(f"Ошибка кадра {i+1}: {e}")

    if len(frames) < 4:
        logger.error("Слишком мало кадров для GIF")
        return None

    gif_buffer = BytesIO()
    frames[0].save(gif_buffer, format="GIF", save_all=True, append_images=frames[1:], duration=350, loop=0, optimize=True)
    gif_buffer.seek(0)
    logger.info(f"🎬 GIF готов, кадров: {len(frames)}")
    return gif_buffer

# ============================================================
# 10. ПАМЯТЬ
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
# 11. ЛИМИТЫ
# ============================================================
MAX_MESSAGES = 9
MSG_REFILL_INTERVAL = timedelta(minutes=25)

MAX_PHOTOS = 3
PHOTO_REFILL_INTERVAL = timedelta(minutes=45)

user_limit_data = {}

def get_limit_data(user_id: int) -> dict:
    if user_id not in user_limit_data:
        user_limit_data[user_id] = {
            "msg_count": MAX_MESSAGES,
            "msg_last_refill": datetime.now(),
            "photo_count": MAX_PHOTOS,
            "photo_last_refill": datetime.now(),
            "vip": False
        }
    return user_limit_data[user_id]

def get_available_messages(user_id: int) -> int:
    data = get_limit_data(user_id)
    if data["vip"]:
        return 999

    now = datetime.now()
    time_passed = now - data["msg_last_refill"]

    if time_passed >= MSG_REFILL_INTERVAL:
        data["msg_count"] = MAX_MESSAGES
        data["msg_last_refill"] = now
        return data["msg_count"]

    minutes_passed = time_passed.total_seconds() / 60
    added = int(minutes_passed / (25 / MAX_MESSAGES))
    if added > 0:
        data["msg_count"] = min(data["msg_count"] + added, MAX_MESSAGES)
        data["msg_last_refill"] = data["msg_last_refill"] + timedelta(minutes=added * (25 / MAX_MESSAGES))

    return data["msg_count"]

def use_message(user_id: int) -> bool:
    data = get_limit_data(user_id)
    if data["vip"]:
        return True

    available = get_available_messages(user_id)
    if available > 0:
        data["msg_count"] = available - 1
        return True
    return False

def get_available_photos(user_id: int) -> int:
    data = get_limit_data(user_id)
    if data["vip"]:
        return 999

    now = datetime.now()
    time_passed = now - data["photo_last_refill"]

    if time_passed >= PHOTO_REFILL_INTERVAL:
        data["photo_count"] = MAX_PHOTOS
        data["photo_last_refill"] = now
        return data["photo_count"]

    minutes_passed = time_passed.total_seconds() / 60
    added = int(minutes_passed / (45 / MAX_PHOTOS))
    if added > 0:
        data["photo_count"] = min(data["photo_count"] + added, MAX_PHOTOS)
        data["photo_last_refill"] = data["photo_last_refill"] + timedelta(minutes=added * (45 / MAX_PHOTOS))

    return data["photo_count"]

def use_photo(user_id: int) -> bool:
    data = get_limit_data(user_id)
    if data["vip"]:
        return True

    available = get_available_photos(user_id)
    if available > 0:
        data["photo_count"] = available - 1
        return True
    return False

def get_time_until_msg_refill(user_id: int) -> str:
    data = get_limit_data(user_id)
    now = datetime.now()
    remaining = MSG_REFILL_INTERVAL - (now - data["msg_last_refill"])
    if remaining.total_seconds() <= 0:
        return "0 мин"
    minutes = int(remaining.total_seconds() // 60)
    return f"{minutes} мин"

def get_time_until_photo_refill(user_id: int) -> str:
    data = get_limit_data(user_id)
    now = datetime.now()
    remaining = PHOTO_REFILL_INTERVAL - (now - data["photo_last_refill"])
    if remaining.total_seconds() <= 0:
        return "0 мин"
    minutes = int(remaining.total_seconds() // 60)
    return f"{minutes} мин"

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
        await bot.reply_to(message, "✅ Ты активировал режим БОГА! 😈🔥")
        logger.info(f"🔥 Админ-режим активирован для {user_id}")
    else:
        await bot.reply_to(message, "❌ Неверный ключ доступа.")

# ============================================================
# 13. КОМАНДА /START
# ============================================================
@bot.message_handler(commands=['start'])
async def handle_start(message):
    user_id = message.from_user.id
    text = (message.text or "").strip().lower()

    if "buy_plus" in text:
        await send_stars_invoice(user_id, "plus")
        return
    if "buy_vip" in text:
        await send_stars_invoice(user_id, "vip")
        return

    clear_history(user_id)
    await bot.reply_to(message, "🌙 Привет, детка! Я — Луна. Напиши мне что-нибудь. 😈")

# ============================================================
# 14. ОТПРАВКА СЧЁТА
# ============================================================
async def send_stars_invoice(user_id: int, plan: str = "plus"):
    try:
        if plan == "vip":
            title = "Luna VIP"
            description = "Всё из Plus + личный менеджер, видео-звонки"
            price = 1500
            payload = "luna_vip"
        else:
            title = "Luna Plus"
            description = "Безлимитные сообщения и фото + приоритетный ответ"
            price = 500
            payload = "luna_plus"

        prices = [LabeledPrice(label=title, amount=price)]

        await bot.send_invoice(
            chat_id=user_id,
            title=title,
            description=description,
            invoice_payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="luna_subscription"
        )
        logger.info(f"💰 Счёт отправлен {user_id}: {title} ({price} Stars)")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки счёта: {e}")
        await bot.send_message(user_id, "❌ Не удалось создать счёт. Попробуй позже.")

# ============================================================
# 15. КОМАНДА /BUY
# ============================================================
@bot.message_handler(commands=['buy'])
async def handle_buy(message):
    user_id = message.from_user.id
    text = (message.text or "").replace('/buy', '').strip().lower()
    plan = "vip" if "vip" in text else "plus"
    await send_stars_invoice(user_id, plan)

# ============================================================
# 16. ОПЛАТА
# ============================================================
@bot.pre_checkout_query_handler(func=lambda query: True)
async def handle_pre_checkout(query):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
async def handle_successful_payment(message):
    user_id = message.from_user.id
    data = get_limit_data(user_id)
    data["vip"] = True
    await bot.reply_to(message, "🎉 Подписка активирована! Безлимит активирован. 😈🔥")
    logger.info(f"💰 Платёж подтверждён: {user_id}")

# ============================================================
# 17. ОБРАБОТЧИКИ
# ============================================================
@bot.message_handler(commands=['clear'])
async def handle_clear(message):
    user_id = message.from_user.id
    if clear_history(user_id):
        await bot.reply_to(message, "🧹 История очищена. 😈")
    else:
        await bot.reply_to(message, "❌ История пуста.")

@bot.message_handler(commands=['photo'])
async def handle_photo(message):
    user_id = message.from_user.id
    if not use_photo(user_id):
        time_left = get_time_until_photo_refill(user_id)
        await bot.reply_to(message, f"🔥 Фото закончились! Попробуй через {time_left}.\nИли купи подписку /buy")
        return
    
    text = (message.text or "").replace('/photo', '').strip() or "Luna, sensual, intimate"
    await bot.reply_to(message, "📸 Делаю фото...")
    prompt = await generate_image_prompt(text)
    image_url = await generate_image(prompt)
    if image_url:
        try:
            await bot.send_photo(chat_id=message.chat.id, photo=image_url, caption="🔥 Твоя Луна. 💋")
        except Exception as e:
            logger.error(f"Ошибка отправки фото: {e}")
            await bot.reply_to(message, "❌ Не удалось отправить фото.")
    else:
        await bot.reply_to(message, "❌ Не удалось сгенерировать фото.")

@bot.message_handler(func=lambda message: message.text is not None and ("скинь" in message.text.lower() or "покажи" in message.text.lower() or "фото" in message.text.lower()))
async def auto_photo(message):
    try:
        user_id = message.from_user.id
        if not use_photo(user_id):
            time_left = get_time_until_photo_refill(user_id)
            await bot.reply_to(message, f"🔥 Фото закончились! Попробуй через {time_left}.\nИли купи подписку /buy")
            return

        user_text = message.text.lower()
        if any(w in user_text for w in ["киск", "пис"]):
            style = "explicit nude, close-up of pussy, legs spread, detailed genitals, intimate, nsfw, realistic"
        elif any(w in user_text for w in ["поп", "жоп"]):
            style = "nude from behind, round ass, bent over, detailed, nsfw, realistic"
        elif any(w in user_text for w in ["груд", "сись"]):
            style = "nude, large natural breasts, detailed nipples, sensual, nsfw, realistic"
        else:
            style = "full body nude, sensual pose, beautiful body, nsfw, realistic"

        await bot.reply_to(message, "📸 Держи...")
        base = "Luna, 20 years old, long dark chestnut hair, green-hazel eyes, pale smooth skin, large natural breasts, narrow waist, wide hips"
        prompt = f"{base}, {style}, photorealistic, 8k, highly detailed"
        image_url = await generate_image(prompt)
        if image_url:
            await bot.send_photo(chat_id=message.chat.id, photo=image_url, caption="🔥 Специально для тебя. 💋")
        else:
            await bot.reply_to(message, "❌ Не удалось сгенерировать фото.")
    except Exception as e:
        logger.error(f"Ошибка авто-фото: {e}")
        await bot.reply_to(message, "❌ Ошибка! Попробуй ещё раз.")

@bot.message_handler(func=lambda m: m.text and any(w in m.text.lower() for w in ["видео", "гиф", "gif", "анимац"]))
async def handle_gif(message):
    try:
        user_id = message.from_user.id
        if not use_photo(user_id):
            time_left = get_time_until_photo_refill(user_id)
            await bot.reply_to(message, f"🔥 Лимит! Попробуй через {time_left}.\nИли /buy")
            return

        await bot.reply_to(message, "🎬 Делаю мини-видео (8 кадров)... Подожди 30–60 сек.")

        user_text = message.text.lower()
        if any(w in user_text for w in ["киск", "пис"]):
            style = "explicit nude, close-up pussy, legs spread, nsfw, realistic"
        elif any(w in user_text for w in ["поп", "жоп"]):
            style = "nude from behind, round ass, nsfw, realistic"
        elif any(w in user_text for w in ["груд", "сись"]):
            style = "nude, large breasts, detailed, nsfw, realistic"
        else:
            style = "full body nude, sensual pose, nsfw, realistic"

        base = "Luna, 20yo, long dark chestnut hair, green-hazel eyes, pale skin, large natural breasts, narrow waist, wide hips"
        prompt_base = f"{base}, {style}"

        gif_data = await generate_gif(prompt_base)
        if gif_data:
            try:
                await bot.send_animation(chat_id=message.chat.id, animation=gif_data, caption="🔥 Мини-видео специально для тебя 💋")
            except Exception as e:
                logger.error(f"Ошибка отправки GIF: {e}")
                await bot.reply_to(message, "❌ Не удалось отправить GIF. Попробуй фото.")
        else:
            await bot.reply_to(message, "❌ Не получилось собрать видео. Попробуй фото.")
    except Exception as e:
        logger.error(f"Ошибка в GIF: {e}")
        await bot.reply_to(message, "❌ Ошибка! Попробуй позже.")

# ============================================================
# 18. ГЕНЕРАЦИЯ ОТВЕТА
# ============================================================
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

# ============================================================
# 19. ОСНОВНОЙ ОБРАБОТЧИК
# ============================================================
user_last_message = {}

@bot.message_handler(func=lambda message: True)
async def handle_message(message):
    if not message.text:
        return

    user_id = message.from_user.id
    user_text = message.text

    now = time.time()
    if user_id in user_last_message:
        if now - user_last_message[user_id] < 0.5:
            return
    user_last_message[user_id] = now

    if user_text.startswith('/'):
        return
    if "скинь" in user_text.lower() or "покажи" in user_text.lower() or "фото" in user_text.lower():
        return

    if not use_message(user_id):
        time_left = get_time_until_msg_refill(user_id)
        await bot.reply_to(message, f"🔥 Луна перегружена! Попробуй через {time_left}.\nИли купи подписку /buy")
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

# ============================================================
# 20. ЗАПУСК
# ============================================================
async def main():
    logger.info("🚀 Запуск веб-сервера...")
    t = Thread(target=run_web, daemon=True)
    t.start()
    
    logger.info("🚀 Луна успешно запущена!")
    logger.info(f"📊 Моделей: {len(MODEL_CHAIN)}")
    logger.info(f"💾 Память: {MAX_HISTORY} сообщений")
    logger.info(f"🔒 Лимит сообщений: {MAX_MESSAGES} / 25 мин")
    logger.info(f"📸 Лимит фото: {MAX_PHOTOS} / 45 мин")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.infinity_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
