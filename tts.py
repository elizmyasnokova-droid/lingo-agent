"""
Text-to-Speech через OpenAI TTS.
Конвертируем MP3 → OGG OPUS для Telegram voice messages.
"""
import io
import logging
import tempfile
import os
from config import OPENAI_API_KEY

logger = logging.getLogger(__name__)


async def text_to_speech(text: str, voice: str = "nova", speed: float = 1.0) -> bytes:
    """
    Конвертировать текст → OGG OPUS (формат для Telegram voice).
    Голоса: alloy, echo, fable, onyx, nova, shimmer
    """
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    try:
        # Получаем MP3 от OpenAI
        response = await client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text[:4000],  # лимит OpenAI
            speed=speed,
            response_format="mp3",
        )
        mp3_bytes = response.content
        logger.info(f"TTS: got {len(mp3_bytes)} bytes of MP3")

        # Конвертируем MP3 → OGG OPUS для Telegram
        ogg_bytes = convert_mp3_to_ogg(mp3_bytes)
        logger.info(f"TTS: converted to {len(ogg_bytes)} bytes of OGG")
        return ogg_bytes

    except Exception as e:
        logger.error(f"TTS error: {e}", exc_info=True)
        raise
    finally:
        await client.close()


def convert_mp3_to_ogg(mp3_bytes: bytes) -> bytes:
    """Конвертировать MP3 в OGG OPUS через pydub."""
    try:
        from pydub import AudioSegment

        with tempfile.TemporaryDirectory() as tmpdir:
            mp3_path = os.path.join(tmpdir, "audio.mp3")
            ogg_path = os.path.join(tmpdir, "audio.ogg")

            with open(mp3_path, "wb") as f:
                f.write(mp3_bytes)

            audio = AudioSegment.from_mp3(mp3_path)
            audio.export(ogg_path, format="ogg", codec="libopus",
                        parameters=["-b:a", "64k"])

            with open(ogg_path, "rb") as f:
                return f.read()

    except Exception as e:
        logger.warning(f"OGG conversion failed: {e} — returning MP3 as fallback")
        return mp3_bytes  # fallback: отправим MP3


async def text_to_speech_slow(text: str) -> bytes:
    """Медленная речь для начинающих (0.8x)."""
    return await text_to_speech(text, speed=0.8)


async def text_to_speech_fast(text: str) -> bytes:
    """Быстрая речь для продвинутых (1.1x)."""
    return await text_to_speech(text, speed=1.1)
