import os
import asyncio
import logging
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile, ReactionTypeEmoji
from google import genai
from google.genai import types as genai_types

TELEGRAM_BOT_TOKEN = os.getenv("tompearl")
GEMINI_API_KEY = os.getenv("golda")

if not GEMINI_API_KEY:
    raise ValueError("Переменная GEMINI_API_KEY не найдена!")

client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

user_chats = {}
user_img_cooldowns = {}
user_msg_cooldowns = {}

IMG_COOLDOWN_SECONDS = 600  # 10 минут для картинок
MSG_COOLDOWN_SECONDS = 10   # 10 секунд медленный режим

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

# Проверка медленного режима (10 сек)
def check_msg_cooldown(user_id: int) -> int:
    current_time = time.time()
    if user_id in user_msg_cooldowns:
        passed = current_time - user_msg_cooldowns[user_id]
        if passed < MSG_COOLDOWN_SECONDS:
            return int(MSG_COOLDOWN_SECONDS - passed) + 1
    user_msg_cooldowns[user_id] = current_time
    return 0

# Функция для безопасной постановки реакции
async def set_like_reaction(chat_id: int, message_id: int):
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji="👍")]
        )
    except Exception:
        pass  # Если у бота нет прав или реакция не поддерживается, просто игнорируем

async def reset_chat(message: types.Message):
    user_id = message.from_user.id
    user_chats[user_id] = create_gemini_chat()
    welcome_text = (
        "Привет, я Google Gemini 3.5 Flash Lite, приятного использования!\n\n"
        "🎨 Чтобы сгенерировать картинку, напиши: <code>/img твой промпт</code>\n"
        "(Генерация картинок — 1 раз в 10 минут, обычные сообщения — 1 раз в 10 секунд)"
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

    if user_id in user_img_cooldowns:
        time_passed = current_time - user_img_cooldowns[user_id]
        if time_passed < IMG_COOLDOWN_SECONDS:
            remaining = int(IMG_COOLDOWN_SECONDS - time_passed)
            minutes, seconds = remaining // 60, remaining % 60
            await message.answer(f"⏳ Подожди еще <b>{minutes} мин {seconds} сек</b> перед следующей генерацией!", parse_mode="HTML")
            return

    prompt = message.text.replace("/img", "", 1).strip()
    if not prompt:
        await message.answer("⚠️ Напиши описание картинки после команды. Пример:\n<code>/img неоновый кот</code>", parse_mode="HTML")
        return

    await set_like_reaction(message.chat.id, message.message_id)
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
        user_img_cooldowns[user_id] = current_time
        photo_file = BufferedInputFile(image_bytes, filename="generated.jpg")
        
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=photo_file,
            caption=f"🖼 <b>Результат по запросу:</b> {prompt}",
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )
        await msg.delete()

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка генерации: {e}")

# ----------------- ОБРАБОТКА СТИКЕРОВ -----------------
@dp.message(F.sticker)
async def sticker_handler(message: types.Message):
    user_id = message.from_user.id
    
    cd = check_msg_cooldown(user_id)
    if cd > 0:
        await message.answer(f"⏳ Медленный режим! Подожди <b>{cd} сек.</b>", parse_mode="HTML")
        return

    await set_like_reaction(message.chat.id, message.message_id)
    
    if user_id not in user_chats:
        user_chats[user_id] = create_gemini_chat()
    
    chat = user_chats[user_id]
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    emoji = message.sticker.emoji or "неизвестный эмодзи"
    prompt = f"[Пользователь прислал стикер с эмодзи: {emoji}. Опиши свою короткую реакцию на этот эмодзи/стикер]"

    try:
        response = chat.send_message(prompt)
        await message.answer(response.text, parse_mode="HTML", reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer(f" Ошибка при обработке стикера: {e}")

# ----------------- ОБРАБОТКА ФОТО -----------------
@dp.message(F.photo)
async def photo_handler(message: types.Message):
    user_id = message.from_user.id

    cd = check_msg_cooldown(user_id)
    if cd > 0:
        await message.answer(f"⏳ Медленный режим! Подожди <b>{cd} сек.</b>", parse_mode="HTML")
        return

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

        response = chat.send_message([image_part, caption])
        await message.answer(response.text, parse_mode="HTML", reply_markup=get_main_keyboard())
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

# ----------------- ОБРАБОТКА ТЕКСТА -----------------
@dp.message(F.text)
async def chat_handler(message: types.Message):
    user_id = message.from_user.id

    cd = check_msg_cooldown(user_id)
    if cd > 0:
        await message.answer(f"⏳ Медленный режим! Подожди <b>{cd} сек.</b>", parse_mode="HTML")
        return

    await set_like_reaction(message.chat.id, message.message_id)

    if user_id not in user_chats:
        user_chats[user_id] = create_gemini_chat()
    
    chat = user_chats[user_id]
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        response = chat.send_message(message.text)
        await message.answer(response.text, parse_mode="HTML", reply_markup=get_main_keyboard())
    except Exception as e:
        try:
            await message.answer(response.text, reply_markup=get_main_keyboard())
        except:
            await message.answer(f"Произошла ошибка: {e}", reply_markup=get_main_keyboard())

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())