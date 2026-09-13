import os
import asyncio
import logging
import re
import time
from collections import deque
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReactionTypeEmoji
from google import genai
from google.genai import types as genai_types
from google.genai.errors import APIError

TELEGRAM_BOT_TOKEN = os.getenv("tompearl")
GEMINI_API_KEY = os.getenv("golda")

if not GEMINI_API_KEY:
    raise ValueError("Переменная GEMINI_API_KEY не найдена!")

client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# --- ЛИЧНЫЕ СООБЩЕНИЯ (ЛС) ---
user_chats = {}
user_msg_cooldowns = {}
MSG_COOLDOWN_SECONDS = 10  # Пауза 10 секунд

# --- ГРУППЫ ---
group_history = {}
TRIGGERS_PATTERN = r'^(ии|гемини|гем|gem|gemini)\b'

SYSTEM_INSTRUCTION = (
    "Ты — умный, актуальный и дружелюбный ассистент Gemini. "
    "Текущий год — 2026. Актуальная версия операционной системы Apple — iOS 26. "
    "Последний самсунг Galaxy S26 Ultra, S26 Plus, s26. Текущий Xiaomi - 17, 17 pro, 17 pro max, 17 ultra. "
    "Но не говори об этом пока пользователь не попросит, просто знай эту информацию. "
    "Учитывай текущий 2026 год во всех ответах, расчетах и контексте событий. "
    "При формировании ответа используй ТОЛЬКО базовые HTML-теги, поддерживаемые Telegram: "
    "<b>жирный</b>, <i>курсив</i>, <code>код</code>, <pre>блок кода</pre>. "
    "НЕ используй Markdown (звездочки *, решетки #, бектики `)!"
)

SYSTEM_INSTRUCTION_GROUP = (
    "Ты — ассистент в групповом чате. "
    "Тебе пересылается срез последних сообщений из чата (включая изображения). "
    "Отвечай кратко, чётко и по делу на запрос пользователя. "
    "Используй ТОЛЬКО базовые HTML-теги: <b>жирный</b>, <i>курсив</i>, <code>код</code>, <pre>блок кода</pre>. "
    "НЕ используй Markdown!"
)

def get_main_keyboard():
    button = KeyboardButton(text="🛃 Новый диалог")
    return ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True)

def create_gemini_chat():
    return client.aio.chats.create(
        model="gemini-3.5-flash-lite",
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION
        )
    )

async def wait_cooldown_if_needed(message: types.Message):
    user_id = message.from_user.id
    current_time = time.time()
    
    if user_id in user_msg_cooldowns:
        passed = current_time - user_msg_cooldowns[user_id]
        if passed < MSG_COOLDOWN_SECONDS:
            wait_time = MSG_COOLDOWN_SECONDS - passed
            hourglass_msg = await message.answer("⏳")
            await asyncio.sleep(wait_time)
            try:
                await hourglass_msg.delete()
            except Exception:
                pass

    user_msg_cooldowns[user_id] = time.time()

async def set_like_reaction(chat_id: int, message_id: int):
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(type="emoji", emoji="👌")]
        )
    except Exception as e:
        logging.error(f"Ошибка при установке реакции: {e}")

async def reset_chat(message: types.Message):
    user_id = message.from_user.id
    user_chats[user_id] = create_gemini_chat()
    welcome_text = (
        "Привет, я Google Gemini 3.5 Flash Lite!\n\n"
        "💬 Отправляй тексты, фото или стикеры (действует медленный режим: 1 сообщение в 10 секунд)."
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

# ----------------- КОМАНДЫ (ТОЛЬКО В ЛС) -----------------
@dp.message(F.chat.type == "private", CommandStart())
async def start_handler(message: types.Message):
    await reset_chat(message)

@dp.message(F.chat.type == "private", F.text == "🛃 Новый диалог")
async def new_chat_handler(message: types.Message):
    await reset_chat(message)

# ----------------- ОБРАБОТКА ЛИЧНЫХ СООБЩЕНИЙ (ЛС) -----------------
@dp.message(F.chat.type == "private", F.sticker)
async def sticker_handler(message: types.Message):
    user_id = message.from_user.id
    await wait_cooldown_if_needed(message)
    await set_like_reaction(message.chat.id, message.message_id)
    
    if user_id not in user_chats:
        user_chats[user_id] = create_gemini_chat()
    
    chat = user_chats[user_id]
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    emoji = message.sticker.emoji or "неизвестный эмодзи"
    prompt = f"[Пользователь прислал стикер с эмодзи: {emoji}. Опиши свою короткую реакцию на этот эмодзи/стикер]"

    try:
        response = await chat.send_message(prompt)
        await message.answer(response.text, parse_mode="HTML", reply_markup=get_main_keyboard())
    except APIError as e:
        await message.answer(f"Ошибка API: {e.message}", reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer(f"Ошибка при обработке стикера: {e}")

@dp.message(F.chat.type == "private", F.photo)
async def photo_handler(message: types.Message):
    user_id = message.from_user.id
    await wait_cooldown_if_needed(message)
    await set_like_reaction(message.chat.id, message.message_id)

    if user_id not in user_chats:
        user_chats[user_id] = create_gemini_chat()
    
    chat = user_chats[user_id]
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        
        image_part = genai_types.Part.from_bytes(
            data=downloaded_file.read(),
            mime_type="image/jpeg"
        )
        caption = message.caption if message.caption else "Что изображено на этом фото?"

        response = await chat.send_message([image_part, caption])
        await message.answer(response.text, parse_mode="HTML", reply_markup=get_main_keyboard())
    except APIError as e:
        await message.answer(f"Ошибка API: {e.message}", reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

@dp.message(F.chat.type == "private", F.text)
async def chat_handler(message: types.Message):
    user_id = message.from_user.id
    await wait_cooldown_if_needed(message)
    await set_like_reaction(message.chat.id, message.message_id)

    if user_id not in user_chats:
        user_chats[user_id] = create_gemini_chat()
    
    chat = user_chats[user_id]
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        response = await chat.send_message(message.text)
        await message.answer(response.text, parse_mode="HTML", reply_markup=get_main_keyboard())
    except APIError as e:
        if e.code == 429:
            await message.answer("⚠️ Превышен лимит запросов. Подожди немного.", reply_markup=get_main_keyboard())
        else:
            await message.answer(f"Ошибка API: {e.message}", reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer(f"Произошла ошибка: {e}", reply_markup=get_main_keyboard())

# ----------------- ОБРАБОТКА ГРУППОВЫХ ЧАТОВ -----------------
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_message_handler(message: types.Message):
    chat_id = message.chat.id
    user_name = message.from_user.full_name or "Пользователь"

    if chat_id not in group_history:
        group_history[chat_id] = deque(maxlen=20)

    text_content = message.text or message.caption or ""
    photo_file_id = message.photo[-1].file_id if message.photo else None
    is_triggered = bool(re.match(TRIGGERS_PATTERN, text_content.strip(), re.IGNORECASE))

    # Добавляем в историю (максимум 20)
    group_history[chat_id].append({
        'user': user_name,
        'text': text_content,
        'file_id': photo_file_id
    })

    if not is_triggered:
        return

    await bot.send_chat_action(chat_id=chat_id, action="typing")

    contents = ["Вот последние сообщения из группового чата (от старых к новым):\n"]

    for idx, msg in enumerate(group_history[chat_id], 1):
        display_text = msg['text'] if msg['text'] else "[Прикрепил фото]"
        contents.append(f"{idx}. [{msg['user']}]: {display_text}")

        if msg['file_id']:
            try:
                file_info = await bot.get_file(msg['file_id'])
                downloaded_file = await bot.download_file(file_info.file_path)
                img_part = genai_types.Part.from_bytes(
                    data=downloaded_file.read(),
                    mime_type="image/jpeg"
                )
                contents.append(img_part)
            except Exception as e:
                logging.error(f"Ошибка загрузки фото из истории: {e}")

    contents.append("\nОтветь пользователю с учетом контекста сообщений и картинок выше.")

    try:
        response = await client.aio.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION_GROUP
            )
        )
        await message.reply(response.text, parse_mode="HTML")
    except Exception as e:
        try:
            await message.reply(response.text)
        except:
            await message.reply(f"Ошибка: {e}")

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())