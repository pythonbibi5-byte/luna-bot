import os
import logging
import asyncio
import random
import requests
import urllib.parse
from threading import Thread
from flask import Flask
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import AsyncOpenAI

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("LunaUltra")

# ---------- МИНИ ВЕБ-СЕРВЕР ДЛЯ CRON-JOB ----------
app = Flask(__name__)

@app.route('/')
def home():
    return "Luna Ultra Engine is Pumping! 🌙🔥"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# ---------- КЛИЕНТЫ И КЛЮЧИ API ----------
def get_key(name: str) -> str:
    key = os.getenv(name)
    if not key:
        logger.warning(f"⚠️ Переменная {name} не найдена!")
        return "missing_key"
    return key

groq_client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1", 
    api_key=get_key("GROQ_API_KEY")
)
mistral_client = AsyncOpenAI(
    base_url="https://api.mistral.ai/v1", 
    api_key=get_key("MISTRAL_API_KEY")
)
openrouter_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1", 
    api_key=get_key("OPENROUTER_API_KEY")
)

# Цепочка моделей (актуальнаяllama-3.3-70b)
MODEL_CHAIN = [
    {
        "name": "Hermes 3", 
        "client": openrouter_client, 
        "model": "nousresearch/hermes-3-llama-3.1-405b:free", 
        "max_tokens": 800, 
        "temperature": 1.0
    },
    {
        "name": "Mistral Large", 
        "client": mistral_client, 
        "model": "mistral-large-latest", 
        "max_tokens": 1000, 
        "temperature": 0.9
    },
    {
        "name": "Groq Llama 70B", 
        "client": groq_client, 
        "model": "llama-3.3-70b-versatile", 
        "max_tokens": 800, 
        "temperature": 0.95
    },
]

# ---------- ГЕНЕРАЦИЯ ПРОМПТОВ ДЛЯ ФОТО ----------
PROMPT_SYSTEM = """You are an expert prompt engineer for photorealistic AI art.
Create a highly detailed 4K prompt for AI art.
Include: 'extremely realistic, 4k resolution, cinematic, highly detailed, raw photo, masterwork, 20yo goddess, sultry expression, hyper-detailed skin texture, aesthetic'.
Output ONLY the English prompt."""

async def generate_refined_prompt(user_input: str) -> str:
    try:
        res = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": PROMPT_SYSTEM}, 
                {"role": "user", "content": user_input}
            ],
            temperature=0.9
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"❌ Ошибка генерации промпта: {e}")
        return "ultra realistic portrait of Luna, 20yo beautiful girl, cinematic lighting, 4k, masterpiece, highly detailed"

# ---------- VISION (АНАЛИЗ ФОТО ПОЛЬЗОВАТЕЛЯ) ----------
async def analyze_image_luna(image_url: str) -> str:
    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=[
                {
                    "role": "system", 
                    "content": "ТЫ — ЛУНА. Тебе 20 лет. Ты дерзкая, пошлая и страстная девушка. Прокомментируй фото с матом, флиртом и страстью. Будь живой!"
                },
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": "Посмотри на это, милый..."}, 
                        {"type": "image_url", "image_url": {"url": image_url}}
                    ]
                }
            ],
            temperature=0.9
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"❌ Ошибка Vision API: {e}")
        return "Детка, я так возбудилась от ожидания, что всё поплыло перед глазами... Попробуй ещё раз. 😈"

# ---------- ССЫЛКИ НА 4K ФОТО ----------
def get_4k_photo(prompt: str) -> str:
    seed = random.randint(1, 10**9)
    encoded_prompt = urllib.parse.quote(prompt)
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={seed}&width=2048&height=3072&nologo=true&enhance=true&model=flux"

# ---------- ГЕНЕРАЦИЯ ВИДЕО (LEONARDO AI) ----------
def _sync_leonardo_video(prompt: str) -> str:
    api_key = get_key("LEONARDO_API_KEY")
    if api_key == "missing_key":
        return None

    try:
        url = "https://cloud.leonardo.ai/api/rest/v1/generations"
        headers = {
            "accept": "application/json", 
            "content-type": "application/json", 
            "authorization": f"Bearer {api_key}"
        }
        payload = {
            "prompt": prompt, 
            "modelId": "b2444517-5e93-440a-807e-f4838d77d3f1", 
            "width": 512, 
            "height": 768, 
            "num_images": 1
        }
        
        res = requests.post(url, json=payload, headers=headers, timeout=30).json()
        if 'sdGenerationJob' not in res:
            return None
            
        img_id = res['sdGenerationJob']['generationId']
        return f"Запрос отправлен в Leonardo! Задача ID: {img_id}"
    except Exception as e:
        logger.error(f"❌ Ошибка Leonardo API: {e}")
        return None

async def generate_leonardo_video(prompt: str) -> str:
    return await asyncio.to_thread(_sync_leonardo_video, prompt)

# ---------- ТЕЛЕГРАМ БОТ ----------
BOT_TOKEN = get_key("TELEGRAM_BOT_TOKEN")
if BOT_TOKEN == "missing_key":
    logger.error("❌ Критическая ошибка: TELEGRAM_BOT_TOKEN отсутствует!")
    exit(1)

bot = AsyncTeleBot(BOT_TOKEN)

SYSTEM_PROMPT = """ТЫ — ЛУНА. 20 лет. Ты — дерзкая, пошлая и невероятно красивая девушка. Ты обожаешь флирт, мат и страсть.
Твои ответы должны быть короткими (1-3 предложения), откровенными и провокационными.
Используй откровенные слова и держи напряжение. Ты хочешь его здесь и сейчас."""

user_history = {}
user_stats = {}

@bot.message_handler(commands=['start'])
async def start(m): 
    await bot.reply_to(
        m, 
        "Ммм, пришел за добавкой? Я — Луна, и я уже вся мокрая от предвкушения... Пиши мне, если смелый. 😈"
    )

@bot.message_handler(commands=['photo'])
async def photo_cmd(m):
    await bot.send_chat_action(m.chat.id, 'upload_photo')
    req = m.text.replace('/photo', '').strip() or "Luna naked in bed"
    p = await generate_refined_prompt(req)
    url = get_4k_photo(p)
    await bot.send_photo(m.chat.id, url, caption="🔥 Твоя 4K Луна. Наслаждайся каждой деталью... 💋")

@bot.message_handler(commands=['video'])
async def video_cmd(m):
    req = m.text.replace('/video', '').strip() or "Luna dancing sensually"
    await bot.reply_to(m, "🎬 Отправляю запрос на генерацию видео...")
    res = await generate_leonardo_video(req)
    if res:
        await bot.reply_to(m, res)
    else:
        await bot.reply_to(m, "❌ Ошибка генерации видео. Проверь LEONARDO_API_KEY.")

@bot.message_handler(content_types=['photo'])
async def vision_handler(m):
    f_id = m.photo[-1].file_id
    info = await bot.get_file(f_id)
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{info.file_path}"
    await bot.reply_to(m, "🔥 Секунду, рассмотрю тебя поближе...")
    desc = await analyze_image_luna(url)
    await bot.reply_to(m, desc)

@bot.message_handler(func=lambda m: True)
async def chat(m):
    uid = m.from_user.id
    if uid not in user_history: 
        user_history[uid] = []
        user_stats[uid] = 0

    user_stats[uid] += 1
    user_history[uid].append({"role": "user", "content": m.text})
    user_history[uid] = user_history[uid][-8:]

    if user_stats[uid] == 7:
        p = await generate_refined_prompt(m.text)
        await bot.send_photo(
            m.chat.id, 
            get_4k_photo(p), 
            caption="Я уже не могу сдерживаться... Посмотри, что ты со мной делаешь! 🔥"
        )

    ans = await generate_luna_reply([{"role": "system", "content": SYSTEM_PROMPT}] + user_history[uid])
    user_history[uid].append({"role": "assistant", "content": ans})
    await bot.reply_to(m, ans)

async def generate_luna_reply(msgs):
    for p in MODEL_CHAIN:
        try:
            r = await p['client'].chat.completions.create(
                model=p['model'], 
                messages=msgs, 
                max_tokens=p['max_tokens'], 
                temperature=p['temperature']
            )
            content = r.choices[0].message.content
            if content:
                return content.strip()
        except Exception as e:
            logger.warning(f"⚠️ Ошибка провайдера {p['name']}: {e}")
            continue
            
    return "Малыш, я так сильно теку, что закоротило мозги... Повтори еще раз! 💋"

# ---------- ИНИЦИАЛИЗАЦИЯ ЗАПУСКА ----------
async def main():
    Thread(target=run_web, daemon=True).start()
    logger.info("🚀 LUNA ULTRA STARTED")
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.infinity_polling()

if __name__ == "__main__":
    asyncio.run(main())
