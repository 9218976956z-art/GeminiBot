import os
import asyncio
import logging
import time
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

user_chats = {}
user_msg_cooldowns = {}
MSG_COOLDOWN_SECONDS = 10  # Пауза 10 секунд

SYSTEM_INSTRUCTION = (
    "Ты — умный и дружелюбный ассистент. "
    "Ты работаешь в Telegram-боте, который автоматически ставит реакцию 👍 на каждое сообщение пользователя, "
    "а также умеет анализировать отправленные фото и стикеры. "
    "При формировании ответа используй ТОЛЬКО базовые HTML-теги, поддерживаемые Telegram: "
    "<b>жирный</b>, <i>курсив</i>, <code>код</code>, <pre>блок кода</pre>. "
    "НЕ используй Markdown (звездочки *, решетки #, бектики `)!"
)

def get_main_keyboard():
    button = KeyboardButton(text="🛃 Новый диалог")
    return ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True)

def create_gemini_chat():
    # Поиск отключен
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
            reaction=[ReactionTypeEmoji(type="emoji", emoji="👍")]
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

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await reset_chat(message)

@dp.message(F.text == "🛃 Новый диалог")
async def new_chat_handler(message: types.Message):
    await reset_chat(message)

# ----------------- ОБРАБОТКА СТИКЕРОВ -----------------
@dp.message(F.sticker)
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

# ----------------- ОБРАБОТКА ФОТО -----------------
@dp.message(F.photo)
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

# ----------------- ОБРАБОТКА ТЕКСТА -----------------
@dp.message(F.text)
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

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())