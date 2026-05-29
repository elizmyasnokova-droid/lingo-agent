"""
🎓 Lingo — AI English tutor bot
"""
import asyncio
import base64
import logging
import os
import tempfile
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message, BufferedInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db
from agent import chat
from config import TELEGRAM_TOKEN
from tts import text_to_speech, text_to_speech_slow
from stt import speech_to_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ─── FSM States ───

class SessionState(StatesGroup):
    speaking = State()
    listening = State()
    vocabulary = State()
    assessment = State()


# ─── Helpers ───

async def get_user_context(user_id: int) -> tuple[str, str]:
    """Вернуть имя и уровень пользователя."""
    user = await db.get_user(user_id)
    name = user.get("first_name") or user.get("username") or "Student"
    level = user.get("level") or "unknown"
    return name, level


async def agent_reply(message: Message, text: str, speak: bool = True, slow: bool = False):
    """Ответить текстом + голосом."""
    await bot.send_chat_action(message.chat.id, "typing")
    user = await db.get_user(message.from_user.id)
    name, level = user.get("first_name", "Student"), user.get("level", "unknown")

    history = await db.get_chat_history(message.from_user.id)
    response = await chat(
        user_id=message.from_user.id,
        message=text,
        history=history,
        user_name=name,
        user_level=level,
    )

    await db.save_message(message.from_user.id, "user", text)
    await db.save_message(message.from_user.id, "assistant", response)

    # Отправляем текст
    await message.answer(response)

    # Отправляем голос если нужно
    if speak:
        await send_voice_response(message, response, slow=slow)

    return response


async def send_voice_response(message: Message, text: str, slow: bool = False):
    """Конвертировать текст в голос и отправить."""
    try:
        await bot.send_chat_action(message.chat.id, "upload_voice")
        # Отправляем только английские части (убираем markdown и русский текст для TTS)
        clean_text = clean_for_tts(text)
        if not clean_text.strip():
            return

        if slow:
            audio = await text_to_speech_slow(clean_text)
        else:
            audio = await text_to_speech(clean_text)

        await bot.send_voice(
            message.chat.id,
            voice=BufferedInputFile(audio, filename="response.mp3"),
        )
    except Exception as e:
        logger.warning(f"Voice send failed: {e}")


def clean_for_tts(text: str) -> str:
    """Убрать markdown символы и оставить только English для TTS."""
    import re
    # Убираем markdown
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'_+', '', text)
    text = re.sub(r'`+', '', text)
    text = re.sub(r'#+\s*', '', text)
    # Убираем эмодзи-линии и разделители
    text = re.sub(r'[-─═]{3,}', '', text)
    # Убираем строки с оценкой (📈 Score: 8/10)
    text = re.sub(r'📈.*?\n', '', text)
    # Убираем строки начинающиеся с эмодзи оценки
    text = re.sub(r'[✅🔧💡]\s*.*?\n', '\n', text)
    text = text.strip()
    # Ограничиваем длину (TTS лимит)
    return text[:2000]


def main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🗣️ Разговорная практика", callback_data="speak"))
    builder.add(InlineKeyboardButton(text="👂 Тренировка слуха", callback_data="listen"))
    builder.add(InlineKeyboardButton(text="📚 Учить слова", callback_data="words"))
    builder.add(InlineKeyboardButton(text="📊 Мой прогресс", callback_data="progress"))
    builder.adjust(1)
    return builder.as_markup()


def topic_keyboard() -> InlineKeyboardMarkup:
    topics = [
        ("✈️ Путешествия", "travel"),
        ("💼 Работа", "work"),
        ("🍕 Еда и рестораны", "food"),
        ("🎬 Фильмы и сериалы", "movies"),
        ("🌍 Культура", "culture"),
        ("🤖 Технологии", "tech"),
        ("💪 Здоровье", "health"),
        ("🎲 Свободная тема", "free"),
    ]
    builder = InlineKeyboardBuilder()
    for label, data in topics:
        builder.add(InlineKeyboardButton(text=label, callback_data=f"topic_{data}"))
    builder.adjust(2)
    return builder.as_markup()


# ─── Commands ───

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    user = await db.get_user(message.from_user.id)
    name = message.from_user.first_name or "there"

    if user and user.get("level") and user["level"] != "unknown":
        level = user["level"]
        await message.answer(
            f"🎓 Welcome back, {name}! Your level: *{level}*\n\n"
            "What would you like to practice today?",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
    else:
        # Новый пользователь — определяем уровень
        await state.set_state(SessionState.assessment)
        greeting = (
            f"🎓 Hi {name}! I'm Lingo, your personal English tutor.\n\n"
            "Before we start, let me quickly assess your level — "
            "it'll take just 2-3 minutes.\n\n"
            "Ready? Let's chat! Tell me a little about yourself — "
            "where are you from and what do you do?"
        )
        await message.answer(greeting)

        # Голосовое приветствие
        try:
            audio = await text_to_speech(
                f"Hi {name}! I'm Lingo, your personal English tutor. "
                "Tell me a little about yourself."
            )
            await bot.send_voice(
                message.chat.id,
                voice=BufferedInputFile(audio, filename="greeting.mp3"),
            )
        except Exception as e:
            logger.warning(f"Greeting voice failed: {e}")

        await db.save_message(message.from_user.id, "assistant", greeting)


@dp.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    name, level = await get_user_context(message.from_user.id)
    await message.answer(
        f"🎓 *Lingo Menu* — Level: *{level}*\n\nChoose what to practice:",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("progress"))
async def cmd_progress(message: Message):
    await db.ensure_user(message.from_user.id)
    user = await db.get_user(message.from_user.id)
    vocab_stats = await db.get_vocab_stats(message.from_user.id)
    session_stats = await db.get_session_stats(message.from_user.id)

    level = user.get("level", "not assessed")
    streak = user.get("streak_days", 0)

    streak_icon = "🔥" if streak >= 7 else ("⭐" if streak >= 3 else "🌱")

    await message.answer(
        f"📊 *Your Progress*\n\n"
        f"🎓 Level: *{level}*\n"
        f"{streak_icon} Streak: *{streak} days*\n\n"
        f"📚 *Vocabulary:*\n"
        f"• Total words: *{vocab_stats['total']}*\n"
        f"• Learned: *{vocab_stats['learned']}*\n"
        f"• Due today: *{vocab_stats['due_today']}*\n\n"
        f"🗣️ *Practice sessions:*\n"
        f"• Total: *{session_stats['total_sessions']}*\n"
        f"• Avg score: *{session_stats['avg_score']}/100*\n"
        f"• Today: *{session_stats['sessions_today']}*",
        parse_mode="Markdown",
    )


@dp.message(Command("words"))
async def cmd_words(message: Message, state: FSMContext):
    await state.clear()
    await db.ensure_user(message.from_user.id)
    words = await db.get_words_for_review(message.from_user.id)

    if not words:
        all_words = await db.get_all_words(message.from_user.id)
        if not all_words:
            await message.answer(
                "📚 *Your vocabulary is empty!*\n\n"
                "Start a speaking session and I'll automatically save new words as we chat.",
                parse_mode="Markdown",
            )
        else:
            await message.answer(
                f"✅ *All caught up!*\n\n"
                f"You have {len(all_words)} words in your dictionary.\n"
                f"No words due for review right now — check back later!",
                parse_mode="Markdown",
            )
        return

    await state.set_state(SessionState.vocabulary)
    await state.update_data(words=words, current_index=0, correct=0, incorrect=0)

    word = words[0]
    await show_word_card(message, word, 1, len(words))


async def show_word_card(message: Message, word: dict, current: int, total: int):
    """Показать карточку слова."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="✅ Знаю", callback_data=f"word_correct_{word['id']}"))
    builder.add(InlineKeyboardButton(text="❌ Не знаю", callback_data=f"word_wrong_{word['id']}"))
    builder.adjust(2)

    text = (
        f"📚 *Word {current}/{total}*\n\n"
        f"🔤 *{word['word']}*\n"
    )
    if word.get("example"):
        text += f"\n💬 _{word['example']}_\n"
    text += "\nDo you know the translation?"

    await message.answer(text, parse_mode="Markdown", reply_markup=builder.as_markup())

    # Произносим слово
    try:
        audio = await text_to_speech(word["word"])
        await bot.send_voice(
            message.chat.id,
            voice=BufferedInputFile(audio, filename="word.mp3"),
        )
    except Exception as e:
        logger.warning(f"Word TTS failed: {e}")


@dp.message(Command("speak"))
async def cmd_speak(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🗣️ *Speaking Practice*\n\nChoose a topic:",
        parse_mode="Markdown",
        reply_markup=topic_keyboard(),
    )


@dp.message(Command("listen"))
async def cmd_listen(message: Message, state: FSMContext):
    await state.clear()
    await db.ensure_user(message.from_user.id)
    await state.set_state(SessionState.listening)

    name, level = await get_user_context(message.from_user.id)
    prompt = (
        f"Start a listening exercise for a {level} student named {name}. "
        "Read a short interesting passage (3-4 sentences) and then ask 2 comprehension questions. "
        "Tell them to answer by voice or text."
    )
    await agent_reply(message, prompt, speak=True, slow=(level in ["A1", "A2"]))


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🎓 *Lingo — Your English Tutor*\n\n"
        "Commands:\n"
        "/menu — main menu\n"
        "/speak — speaking practice\n"
        "/listen — listening exercises\n"
        "/words — vocabulary flashcards\n"
        "/progress — your stats\n"
        "/help — this message\n\n"
        "💡 *Tips:*\n"
        "• Send voice messages to practice speaking\n"
        "• I'll correct your mistakes gently\n"
        "• New words are saved automatically\n"
        "• Practice daily to keep your streak! 🔥",
        parse_mode="Markdown",
    )


# ─── Callbacks ───

@dp.callback_query(F.data == "speak")
async def cb_speak(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "🗣️ Choose a topic:",
        reply_markup=topic_keyboard(),
    )


@dp.callback_query(F.data == "listen")
async def cb_listen(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SessionState.listening)
    name, level = await get_user_context(callback.from_user.id)

    class FakeMessage:
        chat = callback.message.chat
        from_user = callback.from_user
        async def answer(self, text, **kwargs):
            await callback.message.answer(text, **kwargs)

    prompt = (
        f"Start a listening exercise for a {level} student. "
        "Read a short interesting passage and ask comprehension questions."
    )
    await agent_reply(FakeMessage(), prompt, speak=True, slow=(level in ["A1", "A2"]))


@dp.callback_query(F.data == "words")
async def cb_words(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    class FakeMessage:
        chat = callback.message.chat
        from_user = callback.from_user
        async def answer(self, text, **kwargs):
            await callback.message.answer(text, **kwargs)

    await cmd_words(FakeMessage(), state)


@dp.callback_query(F.data == "progress")
async def cb_progress(callback: CallbackQuery):
    await callback.answer()

    class FakeMessage:
        chat = callback.message.chat
        from_user = callback.from_user
        async def answer(self, text, **kwargs):
            await callback.message.answer(text, **kwargs)

    await cmd_progress(FakeMessage())


@dp.callback_query(F.data.startswith("topic_"))
async def cb_topic(callback: CallbackQuery, state: FSMContext):
    topic = callback.data.replace("topic_", "")
    await callback.answer()
    await state.set_state(SessionState.speaking)
    await state.update_data(topic=topic, exchanges=0)

    name, level = await get_user_context(callback.from_user.id)

    class FakeMessage:
        chat = callback.message.chat
        from_user = callback.from_user
        async def answer(self, text, **kwargs):
            await callback.message.answer(text, **kwargs)

    prompt = (
        f"Start a speaking conversation about '{topic}' with {name} (level: {level}). "
        "Ask one engaging opening question. Keep it natural and friendly. "
        "Remind them they can answer by voice or text."
    )
    await agent_reply(FakeMessage(), prompt, speak=True)


@dp.callback_query(F.data.startswith("word_correct_"))
async def cb_word_correct(callback: CallbackQuery, state: FSMContext):
    word_id = int(callback.data.replace("word_correct_", ""))
    await db.update_word_review(word_id, correct=True)
    await callback.answer("✅ Great!")

    data = await state.get_data()
    words = data.get("words", [])
    idx = data.get("current_index", 0) + 1
    correct = data.get("correct", 0) + 1

    if idx >= len(words):
        await finish_vocab_session(callback.message, correct, len(words), state)
    else:
        await state.update_data(current_index=idx, correct=correct)
        word = words[idx]
        await show_word_card(callback.message, word, idx + 1, len(words))


@dp.callback_query(F.data.startswith("word_wrong_"))
async def cb_word_wrong(callback: CallbackQuery, state: FSMContext):
    word_id = int(callback.data.replace("word_wrong_", ""))

    data = await state.get_data()
    words = data.get("words", [])
    idx = data.get("current_index", 0)
    word = words[idx]

    await db.update_word_review(word_id, correct=False)
    await callback.answer("❌ No worries!")

    # Показываем перевод
    await callback.message.answer(
        f"📖 *{word['word']}* = *{word['translation']}*\n"
        + (f"💬 _{word['example']}_" if word.get("example") else ""),
        parse_mode="Markdown",
    )

    idx += 1
    incorrect = data.get("incorrect", 0) + 1

    if idx >= len(words):
        await finish_vocab_session(callback.message, data.get("correct", 0), len(words), state)
    else:
        await state.update_data(current_index=idx, incorrect=incorrect)
        word = words[idx]
        await show_word_card(callback.message, word, idx + 1, len(words))


async def finish_vocab_session(message: Message, correct: int, total: int, state: FSMContext):
    await state.clear()
    score = round((correct / total) * 100) if total > 0 else 0
    await db.save_session(message.chat.id, "vocabulary", score=score)

    emoji = "🏆" if score >= 80 else ("⭐" if score >= 60 else "💪")
    await message.answer(
        f"{emoji} *Vocabulary session complete!*\n\n"
        f"✅ Correct: {correct}/{total}\n"
        f"📈 Score: {score}%\n\n"
        "Keep it up! Words get easier with repetition.",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


# ─── Voice & Text handlers ───

@dp.message(F.voice)
async def handle_voice(message: Message, state: FSMContext):
    await db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await bot.send_chat_action(message.chat.id, "typing")

    # Скачиваем голосовое
    file = await bot.get_file(message.voice.file_id)
    file_bytes = await bot.download_file(file.file_path)
    audio_data = file_bytes.read()

    # Определяем язык распознавания
    current_state = await state.get_state()
    lang = "en-US"  # По умолчанию английский для практики

    # Распознаём
    await message.answer("🎙️ _Listening..._", parse_mode="Markdown")
    text = await speech_to_text(audio_data, language=lang)

    if not text:
        await message.answer(
            "Sorry, I couldn't understand that. "
            "Please try again — speak clearly and not too fast! 🎙️"
        )
        return

    await message.answer(f"🎙️ _You said: \"{text}\"_", parse_mode="Markdown")

    # Если идёт сессия — обрабатываем ответ
    if current_state == SessionState.assessment.state:
        await agent_reply(message, text, speak=False)

    elif current_state == SessionState.speaking.state:
        data = await state.get_data()
        exchanges = data.get("exchanges", 0) + 1
        await state.update_data(exchanges=exchanges)

        # После 6 обменов — завершаем сессию
        if exchanges >= 6:
            prompt = (
                f"User said: '{text}'. "
                "Evaluate this last response briefly, then give a complete session summary: "
                "overall score, main achievements, 2-3 areas to improve. "
                "Be warm and encouraging!"
            )
            await state.clear()
        else:
            prompt = f"User said (via voice): '{text}'. Evaluate and continue the conversation."

        await agent_reply(message, prompt, speak=True)

    elif current_state == SessionState.listening.state:
        prompt = f"Student answered (via voice): '{text}'. Evaluate their answer and continue."
        await agent_reply(message, prompt, speak=True)

    else:
        # Обычный режим — просто чат
        await agent_reply(message, text, speak=True)


@dp.message(F.text)
async def handle_text(message: Message, state: FSMContext):
    current_state = await state.get_state()

    # Игнорируем если FSM для слов
    if current_state == SessionState.vocabulary.state:
        return

    await db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

    speak = current_state in [
        SessionState.speaking.state,
        SessionState.listening.state,
        SessionState.assessment.state,
    ]

    if current_state == SessionState.speaking.state:
        data = await state.get_data()
        exchanges = data.get("exchanges", 0) + 1
        await state.update_data(exchanges=exchanges)

        if exchanges >= 6:
            prompt = (
                f"User wrote: '{message.text}'. "
                "Evaluate briefly, then give session summary with score and tips."
            )
            await state.clear()
        else:
            prompt = f"User wrote: '{message.text}'. Evaluate and continue the conversation."
        await agent_reply(message, prompt, speak=speak)

    else:
        await agent_reply(message, message.text, speak=speak)


# ─── Startup ───

async def set_bot_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="Start / Main menu"),
        BotCommand(command="speak", description="🗣️ Speaking practice"),
        BotCommand(command="listen", description="👂 Listening exercise"),
        BotCommand(command="words", description="📚 Vocabulary flashcards"),
        BotCommand(command="progress", description="📊 My progress"),
        BotCommand(command="menu", description="📋 Menu"),
        BotCommand(command="help", description="Help"),
    ])


async def main():
    await db.init_db()
    await set_bot_commands()
    logger.info("🎓 Lingo Bot started!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
