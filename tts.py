"""
Text-to-Speech через OpenAI TTS.
Голос: nova (женский, естественный, отлично для обучения)
"""
import io
import logging
from openai import AsyncOpenAI
from config import OPENAI_API_KEY

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=OPENAI_API_KEY)


async def text_to_speech(text: str, voice: str = "nova", speed: float = 1.0) -> bytes:
    """
    Конвертировать текст в аудио (MP3).
    Голоса: alloy, echo, fable, onyx, nova, shimmer
    nova — женский, мягкий, отлично для обучения
    speed: 0.75 (медленно) — 1.25 (быстро)
    """
    try:
        response = await client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text,
            speed=speed,
        )
        return response.content
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise


async def text_to_speech_slow(text: str) -> bytes:
    """Медленная речь для начинающих."""
    return await text_to_speech(text, speed=0.8)


async def text_to_speech_fast(text: str) -> bytes:
    """Нормальная скорость для продвинутых."""
    return await text_to_speech(text, speed=1.1)
