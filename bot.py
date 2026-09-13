import os
import asyncio
import logging
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from google import genai
from google.genai import types as genai_types

TELEGRAM_BOT_TOKEN = os.getenv("tompearl")
GEMINI_API_KEY = os.getenv("golda")

client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

user_chats = {}
user_cooldowns = {}
COOLDOWN_SECONDS = 600  # 10 минут

# Системная инструкция для Gemini (чистый HTML под Telegram)
SYSTEM_INSTRUCTION = (
    "Ты — умный и дружелюбный ассистент. "
    "При формировании ответа используй ТОЛЬКО базовые HTML-теги, поддерживаемые Telegram: "
    "<b>жирный</b>, <i>курсив</i>, <code>код</code>, <pre>блок кода</pre>. "
    "НЕ используй Markdown (звездочки *, решетки #, бектики `)! Выводи обычный текст или HTML."
)

def get_main_keyboard():
    button = KeyboardButton(text="🛃 Новый диалог")
    return ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True)

def create_gemini_chat():
    return client.chats.create(
        model="gemini-3.5-flash-lite",
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION
        )
    )

async def reset_chat(message: types.Message):
    user_id = message.from_user.id
    user_chats[user_id] = create_gemini_chat()
    welcome_text = (
        "Привет, я Google Gemini 3.5 Flash Lite, приятного использования!\n\n"
        "🎨 Чтобы сгенерировать картинку, напиши: <code>/img твой промпт</code>\n"
        "(Генерация доступна 1 раз в 10 минут)"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await reset_chat(message)

@dp.message(F.text == "🛃 Новый диалог")
async def new_chat_handler(message: types.Message):
    await reset_chat(message)

# ----------------- ГЕНЕРАЦИЯ КАРТИНКИ (/img) -----------------
@dp.message(Command("img"))
async def generate_image_handler(message: types.Message):
    user_id = message.from_user.id
    current_time = time.time()

    if user_id in user_cooldowns:
        time_passed = current_time - user_cooldowns[user_id]
        if time_passed < COOLDOWN_SECONDS:
            remaining_seconds = int(COOLDOWN_SECONDS - time_passed)
            minutes = remaining_seconds // 60
            seconds = remaining_seconds % 60
            await message.answer(f"⏳ Подожди еще <b>{minutes} мин {seconds} сек</b> перед следующей генерацией!", parse_mode="HTML")
            return

    prompt = message.text.replace("/img", "", 1).strip()
    if not prompt:
        await message.answer("⚠️ Напиши описание картинки после команды. Пример:\n<code>/img неоновый кот</code>", parse_mode="HTML")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    msg = await message.answer("🎨 Генерирую картинку, подожди немного...")

    try:
        result = client.models.generate_images(
            model='imagen-3.0-generate-002',
            prompt=prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                output_mime_type="image/jpeg",
                aspect_ratio="1:1"
            )
        )

        image_bytes = result.generated_images[0].image.image_bytes
        user_cooldowns[user_id] = current_time
        photo_file = BufferedInputFile(image_bytes, filename="generated.jpg")
        
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=photo_file,
            caption=f"🖼 <b>Результат по запросу:</b> {prompt}",
            parse_mode="HTML"
        )
        await msg.delete()

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка генерации: {e}")

# ----------------- ОБРАБОТКА ФОТО -----------------
@dp.message(F.photo)
async def photo_handler(message: types.Message):
    user_id = message.from_user.id
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

        response = chat.send_message([image_part, caption])
        await message.answer(response.text, parse_mode="HTML")
    except Exception as e:
        await message.answer(response.text if 'response' in locals() else f"Ошибка: {e}")

# ----------------- ОБРАБОТКА ТЕКСТА -----------------
@dp.message(F.text)
async def chat_handler(message: types.Message):
    user_id = message.from_user.id
    if user_id not in user_chats:
        user_chats[user_id] = create_gemini_chat()
    
    chat = user_chats[user_id]
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        response = chat.send_message(message.text)
        await message.answer(response.text, parse_mode="HTML")
    except Exception as e:
        # Если случайно попался невалидный тег, отправляем обычным текстом без HTML
        try:
            await message.answer(response.text)
        except:
            await message.answer(f"Произошла ошибка: {e}")

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())