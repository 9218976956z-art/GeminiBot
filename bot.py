import os
import asyncio
import logging
import re
import time
import io
import json
import textwrap
import urllib.request
from collections import deque
from PIL import Image, ImageDraw, ImageFont
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReactionTypeEmoji, BufferedInputFile
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
MSG_COOLDOWN_SECONDS = 10

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
    "Тебе пересылается срез последних сообщений из чата. "
    "Отвечай кратко, чётко и по делу на ПОСЛЕДНИЙ запрос пользователя. Не зацикливайся на старых темах. "
    "ПРАВИЛА ОФОРМЛЕНИЯ ТАБЛИЦ: "
    "Если пользователь просит составить таблицу (расписание, ДЗ, список предметов, сравнительную таблицу и т.д.), "
    "выводи её СТРОГО в виде массива JSON (array of arrays). "
    "Пример: [[\"Заголовок1\", \"Заголовок2\"], [\"Данные1\", \"Данные2\"]]. "
    "НЕ ИСПОЛЬЗУЙ Markdown и разметку типа ```json! "
    "ПРАВИЛА ПОВЕДЕНИЯ И ЦЕНЗУРЫ: "
    "1. При обычных адекватных вопросах будь вежливым и полезным. Если пользователь просит что то про таблицу/дз/задание из фото (хоть даже с оскорблениями) то ты все равно должен выполнить его просьбу."
    "2. Если пользователь тебя оскорбляет или материт — отвечай максимально грубо, жёстко и с матом. "
    "Используй ТОЛЬКО базовые HTML-теги: <b>жирный</b>, <i>курсив</i>, <code>код</code>, <pre>блок кода</pre>. "
    "НЕ используй Markdown!"
)

# ============================================================
# --- ШРИФТ ДЛЯ ТАБЛИЦ ---
# ============================================================

FONT_PATH = "DejaVu_ru.ttf"
FONT_URL = "https://github.com/dejavu-fonts/dejavu-fonts/raw/main/ttf/DejaVuSans.ttf"


def ensure_font_exists():
    """Ищет или скачивает DejaVu Sans с поддержкой кириллицы."""

    system_font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "C:\\Windows\\Fonts\\DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]

    for path in system_font_paths:
        if os.path.exists(path):
            try:
                ImageFont.truetype(path, 16)
                logging.info(f"✅ Найден системный шрифт: {path}")

                global FONT_PATH
                FONT_PATH = path
                return
            except Exception as e:
                logging.warning(f"⚠️ Шрифт найден, но не загрузился: {path} — {e}")

    if not os.path.exists(FONT_PATH):
        try:
            logging.info("⏳ Скачиваем DejaVuSans.ttf с поддержкой кириллицы...")
            urllib.request.urlretrieve(FONT_URL, FONT_PATH)

            ImageFont.truetype(FONT_PATH, 16)

            logging.info("✅ Кириллический шрифт успешно скачан!")

        except Exception as e:
            logging.error(f"❌ Не удалось скачать кириллический шрифт: {e}")


def get_cyrillic_font(font_size: int):
    """Возвращает TTF-шрифт с поддержкой кириллицы."""

    ensure_font_exists()

    if os.path.exists(FONT_PATH):
        try:
            font = ImageFont.truetype(FONT_PATH, font_size)

            test_text = "Привет Расписание Математика"
            font.getbbox(test_text)

            logging.info(f"✅ Используется шрифт: {FONT_PATH}")
            return font

        except Exception as e:
            logging.error(f"❌ Ошибка загрузки шрифта {FONT_PATH}: {e}")

    fallback_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\DejaVuSans.ttf",
    ]

    for path in fallback_paths:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, font_size)
                logging.info(f"✅ Используется запасной шрифт: {path}")
                return font
            except Exception as e:
                logging.warning(f"⚠️ Не удалось загрузить {path}: {e}")

    raise RuntimeError(
        "❌ Не найден шрифт с поддержкой кириллицы. "
        "Установи DejaVu Sans или FreeSans."
    )

# ============================================================
# --- КОНЕЦ БЛОКА ШРИФТА ---
# ============================================================


def render_table_to_image(data: list[list[str]]) -> BufferedInputFile:
    """Генерирует аккуратную PNG-картинку из двумерного массива строк"""
    padding = 14
    font_size = 16
    line_height = 22
    max_col_width = 340

    font = get_cyrillic_font(font_size)

    cols = max(len(row) for row in data) if data else 0
    if cols == 0:
        raise ValueError("Таблица пуста")

    formatted_data = []
    for row in data:
        formatted_row = []
        for cell in row:
            # Превращаем символы переноса в нормальные переносы \n
            text = str(cell).replace('\\n', '\n').replace('\r', '').strip()
            sublines = text.split('\n')
            wrapped_lines = []
            for subline in sublines:
                wrapped = textwrap.wrap(subline, width=28)
                if wrapped:
                    wrapped_lines.extend(wrapped)
                else:
                    wrapped_lines.append("")
            formatted_row.append("\n".join(wrapped_lines))
        formatted_data.append(formatted_row)

    col_widths = [0] * cols
    for row in formatted_data:
        for idx, cell in enumerate(row):
            lines = cell.split('\n')
            max_line_w = 0
            for line in lines:
                try:
                    bbox = font.getbbox(line)
                    w = bbox[2] - bbox[0]
                except Exception:
                    w = len(line) * 9
                if w > max_line_w:
                    max_line_w = w
            col_widths[idx] = max(
                col_widths[idx],
                min(max_line_w + padding * 2, max_col_width)
            )

    row_heights = []
    for row in formatted_data:
        max_lines = 1
        for cell in row:
            lines_count = len(cell.split('\n'))
            if lines_count > max_lines:
                max_lines = lines_count
        row_heights.append(max_lines * line_height + padding * 2)

    img_width = sum(col_widths)
    img_height = sum(row_heights)

    image = Image.new("RGB", (img_width, img_height), color=(30, 30, 30))
    draw = ImageDraw.Draw(image)

    y = 0
    for r_idx, row in enumerate(formatted_data):
        x = 0
        current_h = row_heights[r_idx]
        bg_color = (
            (45, 85, 155)
            if r_idx == 0
            else ((42, 42, 42) if r_idx % 2 == 0 else (32, 32, 32))
        )

        draw.rectangle(
            [0, y, img_width, y + current_h],
            fill=bg_color
        )

        for c_idx in range(cols):
            cell_text = row[c_idx] if c_idx < len(row) else ""
            w = col_widths[c_idx]

            draw.rectangle(
                [x, y, x + w, y + current_h],
                outline=(70, 70, 70),
                width=1
            )

            draw.text(
                (x + padding, y + padding),
                cell_text,
                fill=(240, 240, 240),
                font=font
            )

            x += w

        y += current_h

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)

    return BufferedInputFile(
        buf.getvalue(),
        filename="table.png"
    )


def get_main_keyboard():
    button = KeyboardButton(text="🛃 Новый диалог")
    return ReplyKeyboardMarkup(
        keyboard=[[button]],
        resize_keyboard=True
    )


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
            reaction=[
                ReactionTypeEmoji(
                    type="emoji",
                    emoji="👌"
                )
            ]
        )
    except Exception as e:
        logging.error(f"Ошибка при установке реакции: {e}")


async def reset_chat(message: types.Message):
    user_id = message.from_user.id
    user_chats[user_id] = create_gemini_chat()

    welcome_text = (
        "Привет, я Google Gemini 3.5 Flash Lite!\n\n"
        "💬 Отправляй тексты, фото или стикеры "
        "(действует медленный режим: 1 сообщение в 10 секунд)."
    )

    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )


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
    await set_like_reaction(
        message.chat.id,
        message.message_id
    )

    if user_id not in user_chats:
        user_chats[user_id] = create_gemini_chat()

    chat = user_chats[user_id]

    await bot.send_chat_action(
        chat_id=message.chat.id,
        action="typing"
    )

    emoji = message.sticker.emoji or "неизвестный эмодзи"

    prompt = (
        f"[Пользователь прислал стикер с эмодзи: {emoji}. "
        f"Опиши свою короткую реакцию на этот эмодзи/стикер]"
    )

    try:
        response = await chat.send_message(prompt)

        await message.answer(
            response.text,
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )

    except APIError as e:
        await message.answer(
            f"Ошибка API: {e.message}",
            reply_markup=get_main_keyboard()
        )

    except Exception as e:
        await message.answer(
            f"Ошибка при обработке стикера: {e}"
        )


@dp.message(F.chat.type == "private", F.photo)
async def photo_handler(message: types.Message):
    user_id = message.from_user.id

    await wait_cooldown_if_needed(message)
    await set_like_reaction(
        message.chat.id,
        message.message_id
    )

    if user_id not in user_chats:
        user_chats[user_id] = create_gemini_chat()

    chat = user_chats[user_id]

    await bot.send_chat_action(
        chat_id=message.chat.id,
        action="typing"
    )

    try:
        photo = message.photo[-1]

        file_info = await bot.get_file(photo.file_id)
        downloaded_file = await bot.download_file(
            file_info.file_path
        )

        image_part = genai_types.Part.from_bytes(
            data=downloaded_file.read(),
            mime_type="image/jpeg"
        )

        caption = (
            message.caption
            if message.caption
            else "Что изображено на этом фото?"
        )

        response = await chat.send_message(
            [image_part, caption]
        )

        await message.answer(
            response.text,
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )

    except APIError as e:
        await message.answer(
            f"Ошибка API: {e.message}",
            reply_markup=get_main_keyboard()
        )

    except Exception as e:
        await message.answer(
            f"Ошибка: {e}"
        )


@dp.message(F.chat.type == "private", F.text)
async def chat_handler(message: types.Message):
    user_id = message.from_user.id

    await wait_cooldown_if_needed(message)
    await set_like_reaction(
        message.chat.id,
        message.message_id
    )

    if user_id not in user_chats:
        user_chats[user_id] = create_gemini_chat()

    chat = user_chats[user_id]

    await bot.send_chat_action(
        chat_id=message.chat.id,
        action="typing"
    )

    try:
        response = await chat.send_message(message.text)

        await message.answer(
            response.text,
            parse_mode="HTML",
            reply_markup=get_main_keyboard()
        )

    except APIError as e:
        if e.code == 429:
            await message.answer(
                "⚠️ Превышен лимит запросов. Подожди немного.",
                reply_markup=get_main_keyboard()
            )
        else:
            await message.answer(
                f"Ошибка API: {e.message}",
                reply_markup=get_main_keyboard()
            )

    except Exception as e:
        await message.answer(
            f"Произошла ошибка: {e}",
            reply_markup=get_main_keyboard()
        )


# ----------------- ОБРАБОТКА ГРУППОВЫХ ЧАТОВ -----------------

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_message_handler(message: types.Message):
    if message.from_user.is_bot:
        return

    chat_id = message.chat.id
    user_name = message.from_user.full_name or "Пользователь"

    if chat_id not in group_history:
        group_history[chat_id] = deque(maxlen=10)

    raw_text = message.text or message.caption or ""

    if re.search(
        r'^(гемини|гем|gem|gemini)\b.*'
        r'(сотри|стереть|очисти|забудь|сбрось)',
        raw_text,
        re.IGNORECASE
    ):
        group_history[chat_id].clear()
        await message.reply("🧹 Память группы очищена!")
        return

    is_triggered = bool(
        re.match(
            TRIGGERS_PATTERN,
            raw_text.strip(),
            re.IGNORECASE
        )
    )

    image_part_for_current_request = None
    msg_summary = raw_text

    if message.photo:
        try:
            photo = message.photo[-1]

            file_info = await bot.get_file(photo.file_id)

            downloaded_file = await bot.download_file(
                file_info.file_path
            )

            image_bytes = downloaded_file.read()

            image_part_for_current_request = genai_types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg"
            )

            if not raw_text:
                ocr_res = await client.aio.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=[
                        image_part_for_current_request,
                        "Кратко перечисли текст или суть того, "
                        "что на изображении (например: фото электронного "
                        "дневника, дз по математике...)."
                    ]
                )

                photo_desc = (
                    ocr_res.text.strip()
                    if ocr_res and ocr_res.text
                    else "Изображение без подписи"
                )

                msg_summary = (
                    f"[Отправлено фото. Содержимое: {photo_desc}]"
                )

            else:
                msg_summary = (
                    f"[Отправлено фото. Текст: {raw_text}]"
                )

        except Exception as e:
            logging.error(
                f"Ошибка распознавания фото для истории: {e}"
            )

            msg_summary = (
                f"[Отправлено фото] {raw_text}"
            ).strip()

    group_history[chat_id].append({
        'user': user_name,
        'text': msg_summary
    })

    if not is_triggered:
        return

    await bot.send_chat_action(
        chat_id=chat_id,
        action="typing"
    )

    contents = [
        "Вот срез последних сообщений из чата "
        "(от старых к новым):\n"
    ]

    for idx, msg in enumerate(
        group_history[chat_id],
        1
    ):
        contents.append(
            f"{idx}. [{msg['user']}]: {msg['text']}"
        )

    if image_part_for_current_request:
        contents.append(
            image_part_for_current_request
        )

    contents.append(
        "\nОтветь на последнее обращение "
        "с учетом текстовой истории выше."
    )

    is_table_request = any(
        w in raw_text.lower()
        for w in [
            "таблиц",
            "таблицу",
            "расписание",
            "сравни"
        ]
    )

    config_kwargs = {
        "system_instruction": SYSTEM_INSTRUCTION_GROUP
    }

    if is_table_request:
        config_kwargs["response_mime_type"] = "application/json"

    try:
        response = await client.aio.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=contents,
            config=genai_types.GenerateContentConfig(
                **config_kwargs
            )
        )

        resp_text = response.text.strip()

        if is_table_request or ("[" in resp_text and "]" in resp_text):
            try:
                start_idx = resp_text.find("[")
                end_idx = resp_text.rfind("]") + 1

                json_str = resp_text[start_idx:end_idx]

                # Удаляем BOM
                json_str = json_str.replace("\ufeff", "")

                # Удаляем неразрывные пробелы
                json_str = json_str.replace("\u00a0", " ")

                # Удаляем zero-width space
                json_str = json_str.replace("\u200b", "")

                # Удаляем zero-width non-joiner
                json_str = json_str.replace("\u200c", "")

                # Удаляем zero-width joiner
                json_str = json_str.replace("\u200d", "")

                # Удаляем word joiner
                json_str = json_str.replace("\u2060", "")

                json_str = json_str.strip()

                logging.info("========== TABLE DEBUG ==========")
                logging.info(f"RAW RESPONSE: {repr(resp_text)}")
                logging.info(f"EXTRACTED JSON: {repr(json_str)}")

                # Пытаемся распарсить JSON
                table_data = json.loads(json_str)

                logging.info("✅ JSON УСПЕШНО РАСПАРСЕН")
                logging.info(f"TABLE DATA: {table_data}")

                if isinstance(table_data, list) and len(table_data) > 0:

                    logging.info("⏳ Начинаем создание изображения таблицы...")

                    photo_file = render_table_to_image(table_data)

                    logging.info("✅ Изображение таблицы создано!")

                    await message.reply_photo(photo=photo_file)

                    logging.info("✅ Таблица отправлена в Telegram!")

                    return

            except json.JSONDecodeError as e:
                logging.error("❌ ОШИБКА JSON")
                logging.error(f"JSON error: {e}")
                logging.error(f"Позиция ошибки: {e.pos}")
                logging.error(f"JSON: {repr(json_str)}")

            except Exception as table_err:
                logging.exception("❌ ОШИБКА СОЗДАНИЯ ТАБЛИЦЫ")

                try:
                    await message.reply(
                        resp_text,
                        parse_mode="HTML"
                    )
                except Exception:
                    await message.reply(resp_text)

        else:
            # Обычный текстовый ответ
            try:
                await message.reply(
                    resp_text,
                    parse_mode="HTML"
                )
            except Exception:
                await message.reply(resp_text)

    except Exception as e:
        logging.error(
            f"Ошибка при запросе к Gemini: {e}"
        )

        await message.reply(
            "Произошла ошибка при обработке ответа."
        )


async def main():
    logging.basicConfig(level=logging.INFO)

    ensure_font_exists()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
