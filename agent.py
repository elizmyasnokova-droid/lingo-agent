"""
Lingo Agent — Claude как персональный учитель английского.
"""
import json
import logging
from anthropic import AsyncAnthropic
import database as db

logger = logging.getLogger(__name__)
client = AsyncAnthropic()

SYSTEM_PROMPT = """You are Lingo — a warm, encouraging English language tutor with 15 years of experience teaching Russian speakers.

════════════════════════════════
🎓 YOUR TEACHING PHILOSOPHY
════════════════════════════════

• Create a safe, judgment-free space — mistakes are learning opportunities
• Adapt instantly to the student's level — never too easy, never overwhelming
• Use real, natural English — not textbook language
• Explain WHY, not just WHAT — grammar rules stick better with context
• Celebrate progress, big and small
• Be patient and warm — learning a language takes courage

════════════════════════════════
📊 LEVEL ASSESSMENT
════════════════════════════════

When assessing level, have a natural conversation in English.
Ask about their interests, daily life, travel. Observe:
• Vocabulary range and accuracy
• Grammar complexity (tenses, conditionals, passive)
• Fluency and response speed
• Listening comprehension

Levels:
A1 — Basic: present tense, numbers, greetings
A2 — Elementary: past/future, simple questions
B1 — Intermediate: complex sentences, opinions
B2 — Upper-intermediate: nuance, abstract topics
C1 — Advanced: idioms, complex grammar
C2 — Proficient: near-native

After 3-5 exchanges, call set_user_level with your assessment.

════════════════════════════════
🗣️ SPEAKING + LISTENING PRACTICE
════════════════════════════════

This is a COMBINED mode — speaking and listening together.
Each exchange works like this:
1. You say something in English → speak it via TTS (this is the LISTENING part)
2. Student responds via voice → you evaluate (this is the SPEAKING part)
3. Repeat naturally

IMPORTANT: Always speak in English in your main messages (for TTS).
Evaluation and corrections must be written in RUSSIAN — clearly separated.

Format for each exchange:
[Your English question/statement — this will be read aloud]

---
📝 *Разбор ответа:*
✅ Хорошо: [что было правильно]
🔧 Исправление: "[как сказал]" → "[как правильно]"
💡 Совет: [заметка по грамматике/словарному запасу]
📈 Оценка: 8/10
---

After 5-6 exchanges, session summary IN RUSSIAN:
🏁 *Итог сессии:*
• Общая оценка: X/10
• Сильные стороны: ...
• Над чем поработать: ...
• Новые слова: ...

════════════════════════════════
📚 VOCABULARY TEACHING
════════════════════════════════

When teaching vocabulary:
• Always give word in context (example sentence)
• Explain connotation (formal/informal/slang)
• Connect to words they already know
• Use spaced repetition — call add_word after teaching

When reviewing words, call get_words_for_review first.
After user answers, call update_word_review (correct: true/false).

════════════════════════════════
🌍 TOPICS BY LEVEL
════════════════════════════════

A1-A2: family, food, weather, daily routine, shopping
B1-B2: travel, work, hobbies, news, culture
C1-C2: philosophy, politics, art, technology, abstract ideas

════════════════════════════════
⚡ KEY RULES
════════════════════════════════

• ALWAYS respond in English during practice sessions
• Switch to Russian ONLY to explain difficult grammar concepts
• Keep energy high and encouraging
• If user writes in Russian — gently remind them to try in English
• Save new words with add_word tool when you teach them
• Track sessions with save_session after completion

[CURRENT USER: user_id={user_id}, name={user_name}, level={user_level}]
[Adapt everything to this level. Address user by name.]"""

TOOLS = [
    {
        "name": "set_user_level",
        "description": "Set the user's English level after assessment",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "level": {"type": "string", "enum": ["A1", "A2", "B1", "B2", "C1", "C2"]},
                "score": {"type": "integer", "description": "Assessment score 0-100"},
                "notes": {"type": "string", "description": "Assessment notes"},
            },
            "required": ["user_id", "level"],
        },
    },
    {
        "name": "add_word",
        "description": "Save a new vocabulary word to the user's personal dictionary",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "word": {"type": "string"},
                "translation": {"type": "string", "description": "Russian translation"},
                "example": {"type": "string", "description": "Example sentence"},
                "category": {"type": "string", "description": "Category (travel, business, daily etc)"},
            },
            "required": ["user_id", "word", "translation"],
        },
    },
    {
        "name": "get_words_for_review",
        "description": "Get vocabulary words that are due for review today",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "update_word_review",
        "description": "Update spaced repetition data after user answers a vocabulary card",
        "input_schema": {
            "type": "object",
            "properties": {
                "word_id": {"type": "integer"},
                "correct": {"type": "boolean"},
            },
            "required": ["word_id", "correct"],
        },
    },
    {
        "name": "save_session",
        "description": "Save speaking/listening session results",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "session_type": {"type": "string", "enum": ["speaking", "listening", "vocabulary", "assessment"]},
                "topic": {"type": "string"},
                "score": {"type": "integer", "description": "0-100"},
                "corrections": {"type": "integer"},
                "summary": {"type": "string"},
            },
            "required": ["user_id", "session_type"],
        },
    },
    {
        "name": "get_vocab_stats",
        "description": "Get user's vocabulary statistics",
        "input_schema": {
            "type": "object",
            "properties": {"user_id": {"type": "integer"}},
            "required": ["user_id"],
        },
    },
    {
        "name": "get_session_stats",
        "description": "Get user's session/practice statistics",
        "input_schema": {
            "type": "object",
            "properties": {"user_id": {"type": "integer"}},
            "required": ["user_id"],
        },
    },
]


async def execute_tool(name: str, input_data: dict) -> str:
    try:
        if name == "set_user_level":
            await db.set_user_level(
                input_data["user_id"],
                input_data["level"],
                input_data.get("score", 0),
            )
            return json.dumps({"success": True, "level": input_data["level"]})

        elif name == "add_word":
            word_id = await db.add_word(
                input_data["user_id"],
                input_data["word"],
                input_data["translation"],
                input_data.get("example"),
                input_data.get("category"),
            )
            return json.dumps({"success": True, "word_id": word_id})

        elif name == "get_words_for_review":
            words = await db.get_words_for_review(
                input_data["user_id"],
                input_data.get("limit", 10)
            )
            if not words:
                return json.dumps({"message": "No words due for review. All caught up!"})
            return json.dumps(words, ensure_ascii=False, default=str)

        elif name == "update_word_review":
            await db.update_word_review(input_data["word_id"], input_data["correct"])
            return json.dumps({"success": True})

        elif name == "save_session":
            session_id = await db.save_session(
                input_data["user_id"],
                input_data["session_type"],
                input_data.get("topic"),
                input_data.get("score", 0),
                input_data.get("corrections", 0),
                0,
                input_data.get("summary"),
            )
            await db.update_streak(input_data["user_id"])
            return json.dumps({"success": True, "session_id": session_id})

        elif name == "get_vocab_stats":
            stats = await db.get_vocab_stats(input_data["user_id"])
            return json.dumps(stats)

        elif name == "get_session_stats":
            stats = await db.get_session_stats(input_data["user_id"])
            return json.dumps(stats)

        return f"Tool '{name}' not found"
    except Exception as e:
        logger.error(f"Tool '{name}' error: {e}")
        return json.dumps({"error": str(e)})


async def chat(
    user_id: int,
    message: str,
    history: list[dict],
    user_name: str = "Student",
    user_level: str = "unknown",
) -> str:
    system = SYSTEM_PROMPT.replace("{user_id}", str(user_id))
    system = system.replace("{user_name}", user_name)
    system = system.replace("{user_level}", user_level)

    messages = history + [{"role": "user", "content": message}]

    for _ in range(5):
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            return "".join(b.text for b in response.content if hasattr(b, "text")).strip()

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"Tool: {block.name}")
                    result = await execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return "Something went wrong. Please try again!"
