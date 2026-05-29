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
    await do_agent_reply(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        text=text,
        speak=speak,
        slow=slow,
        reply_func=message.answer,
    )


async def do_agent_reply(chat_id: int, user_id: int, text: str,
                          speak: bool, slow: bool, reply_func):
    """Основная логика — принимает chat_id и user_id напрямую."""
    await bot.send_chat_action(chat_id, "typing")
    user = await db.get_user(user_id)
    name = (user or {}).get("first_name") or "Student"
    level = (user or {}).get("level") or "unknown"

    history = await db.get_chat_history(user_id)
    response = await chat(
        user_id=user_id,
        message=text,
        history=history,
        user_name=name,
        user_level=level,
    )

    await db.save_message(user_id, "user", text)
    await db.save_message(user_id, "assistant", response)

    await reply_func(response)

    if speak:
        await send_voice_to_chat(chat_id, response, slow=slow)

    return response


async def send_voice_response(message: Message, text: str, slow: bool = False):
    """Обёртка для совместимости."""
    await send_voice_to_chat(message.chat.id, text, slow=slow)


async def send_voice_to_chat(chat_id: int, text: str, slow: bool = False):
    """Конвертировать текст в голос и отправить в чат."""
    try:
        clean_text = clean_for_tts(text)
        if not clean_text.strip():
            logger.warning("TTS: clean_for_tts returned empty string")
            return

        logger.info(f"TTS: sending voice, text length={len(clean_text)}")
        await bot.send_chat_action(chat_id, "upload_voice")

        audio = await text_to_speech_slow(clean_text) if slow else await text_to_speech(clean_text)

        if not audio:
            logger.warning("TTS: got empty audio bytes")
            return

        # Пробуем send_voice (OGG OPUS)
        try:
            await bot.send_voice(
                chat_id,
                voice=BufferedInputFile(audio, filename="voice.ogg"),
            )
            logger.info("TTS: voice sent successfully")
        except Exception as voice_err:
            logger.warning(f"send_voice failed: {voice_err} — trying send_audio")
            # Fallback: send_audio принимает MP3
            await bot.send_audio(
                chat_id,
                audio=BufferedInputFile(audio, filename="response.mp3"),
            )

    except Exception as e:
        logger.error(f"Voice send failed completely: {e}", exc_info=True)


def clean_for_tts(text: str) -> str:
    """
    Для TTS берём только English часть — до блока с русской оценкой.
    Русская оценка идёт после разделителя ---.
    """
    import re

    # Если есть блок оценки (---) — берём только часть ДО него
    if "---" in text:
        english_part = text.split("---")[0]
    else:
        english_part = text

    # Убираем markdown символы
    english_part = re.sub(r"\*+", "", english_part)
    english_part = re.sub(r"_+", "", english_part)
    english_part = re.sub(r"`+", "", english_part)
    english_part = re.sub(r"#+\s*", "", english_part)
    english_part = re.sub(r"[-─═]{3,}", "", english_part)

    # Убираем строки полностью на русском (кириллица)
    lines = english_part.split("\n")
    english_lines = []
    for line in lines:
        # Если строка содержит кириллицу — пропускаем
        if re.search(r"[а-яёА-ЯЁ]", line):
            continue
        english_lines.append(line)

    result = "\n".join(english_lines).strip()
    return result[:2000] if result else ""


def main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🎯 Практика (говорение + слух)", callback_data="speak"))
    builder.add(InlineKeyboardButton(text="📚 Учить слова", callback_data="words"))
    builder.add(InlineKeyboardButton(text="📊 Мой прогресс", callback_data="progress"))
    builder.adjust(1)
    return builder.as_markup()


def topic_keyboard() -> InlineKeyboardMarkup:
    topics = [
        ("✈️ Путешествия", "travel"),
        ("💼 Работа и карьера", "work"),
        ("🍕 Еда и рестораны", "food"),
        ("🎬 Фильмы и сериалы", "movies"),
        ("🌍 Культура и традиции", "culture"),
        ("🤖 Технологии", "tech"),
        ("💪 Здоровье и спорт", "health"),
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


def themes_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора темы для слов."""
    from vocabulary_data import VOCABULARY_THEMES
    builder = InlineKeyboardBuilder()
    for key, theme in VOCABULARY_THEMES.items():
        builder.add(InlineKeyboardButton(
            text=theme["name"],
            callback_data=f"theme_{key}"
        ))
    builder.add(InlineKeyboardButton(text="🔄 Все темы (смешанные)", callback_data="theme_all"))
    builder.add(InlineKeyboardButton(text="📖 Мои сохранённые слова", callback_data="theme_saved"))
    builder.adjust(2)
    return builder.as_markup()


@dp.message(Command("words"))
async def cmd_words(message: Message, state: FSMContext):
    await state.clear()
    await db.ensure_user(message.from_user.id)

    vocab_stats = await db.get_vocab_stats(message.from_user.id)
    total = vocab_stats["total"]
    due = vocab_stats["due_today"]

    text = "📚 *Словарный запас*\n\n"
    if total > 0:
        text += f"📖 Всего слов: *{total}*\n"
        text += f"🔔 Пора повторить: *{due}*\n\n"
    text += "Выбери тему для изучения:"

    await message.answer(text, parse_mode="Markdown", reply_markup=themes_keyboard())


@dp.callback_query(F.data.startswith("theme_"))
async def cb_theme_select(callback: CallbackQuery, state: FSMContext):
    theme_key = callback.data.replace("theme_", "")
    await callback.answer()
    user_id = callback.from_user.id

    if theme_key == "all":
        # Смешанные — все слова на повторение
        words = await db.get_words_for_review(user_id, limit=20)
        if not words:
            await callback.message.answer(
                "✅ Отлично! Нет слов для повторения.\n\n"
                "Выбери тему чтобы загрузить новые слова!"
            )
            return
        theme_name = "Смешанные темы"

    elif theme_key == "saved":
        # Только сохранённые агентом слова (без category)
        all_words = await db.get_all_words(user_id)
        words = [w for w in all_words if not w.get("category") or
                 w["category"] not in ["travel","food","work","daily","emotions",
                                        "health","technology","shopping","people","nature"]]
        if not words:
            await callback.message.answer(
                "📖 Сохранённых слов пока нет.\n\n"
                "Они появятся автоматически во время разговорной практики!"
            )
            return
        words = [w for w in words if w.get("next_review", "") <= __import__("datetime").datetime.now().isoformat()][:20]
        theme_name = "Мои сохранённые слова"

    else:
        from vocabulary_data import VOCABULARY_THEMES
        theme = VOCABULARY_THEMES.get(theme_key)
        if not theme:
            return
        theme_name = theme["name"]

        # Загружаем тему если ещё не загружена
        added = await db.seed_theme_vocabulary(user_id, theme_key)
        if added > 0:
            await callback.message.answer(
                f"✨ Загружено *{added} новых слов* по теме {theme_name}!",
                parse_mode="Markdown"
            )

        words = await db.get_words_by_theme(user_id, theme_key, limit=20)
        if not words:
            await callback.message.answer(
                f"✅ По теме {theme_name} всё повторено!\n"
                "Слова появятся снова через несколько дней."
            )
            return

    await state.set_state(SessionState.vocabulary)
    await state.update_data(
        words=words,
        current_index=0,
        correct=0,
        incorrect=0,
        theme_name=theme_name
    )
    await callback.message.answer(
        f"📚 Тема: *{theme_name}*\n"
        f"Слов для изучения: *{len(words)}*\n\n"
        "Слушай произношение и проверяй знание! 🎯",
        parse_mode="Markdown"
    )
    await show_word_card(callback.message, words[0], 1, len(words))


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
    """Слух и говорение теперь объединены — редиректим в /speak."""
    await cmd_speak(message, state)


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
    await callback.message.answer("🎯 Выбери тему для практики:", reply_markup=topic_keyboard())


@dp.callback_query(F.data == "words")
async def cb_words(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    user_id = callback.from_user.id
    await db.ensure_user(user_id)
    vocab_stats = await db.get_vocab_stats(user_id)
    total = vocab_stats["total"]
    due = vocab_stats["due_today"]
    text = "📚 *Словарный запас*\n\n"
    if total > 0:
        text += f"📖 Всего слов: *{total}*\n"
        text += f"🔔 Пора повторить: *{due}*\n\n"
    text += "Выбери тему для изучения:"
    await callback.message.answer(text, parse_mode="Markdown", reply_markup=themes_keyboard())


@dp.callback_query(F.data == "progress")
async def cb_progress(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    await db.ensure_user(user_id)
    user = await db.get_user(user_id)
    vocab_stats = await db.get_vocab_stats(user_id)
    session_stats = await db.get_session_stats(user_id)
    level = user.get("level", "not assessed")
    streak = user.get("streak_days", 0)
    streak_icon = "🔥" if streak >= 7 else ("⭐" if streak >= 3 else "🌱")
    await callback.message.answer(
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


@dp.callback_query(F.data.startswith("topic_"))
async def cb_topic(callback: CallbackQuery, state: FSMContext):
    topic = callback.data.replace("topic_", "")
    await callback.answer()
    await state.set_state(SessionState.speaking)
    await state.update_data(topic=topic, exchanges=0)

    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    user = await db.get_user(user_id)
    name = (user or {}).get("first_name") or "Student"
    level = (user or {}).get("level") or "unknown"

    topic_names = {
        "travel": "путешествия", "work": "работа", "food": "еда",
        "movies": "кино", "culture": "культура", "tech": "технологии",
        "health": "здоровье", "free": "свободная тема"
    }
    topic_ru = topic_names.get(topic, topic)

    await callback.message.answer(
        f"🎯 Тема: *{topic_ru}*\n\nНачинаем! Отвечай голосом или текстом на английском 🎙️",
        parse_mode="Markdown"
    )

    prompt = (
        f"Start a combined speaking+listening practice about '{topic}' with {name} (level: {level}). "
        "Say 2-3 interesting sentences about this topic in English (listening practice), "
        "then ask ONE clear question to start the conversation."
    )

    await do_agent_reply(
        chat_id=chat_id,
        user_id=user_id,
        text=prompt,
        speak=True,
        slow=(level in ["A1", "A2"]),
        reply_func=callback.message.answer,
    )


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
