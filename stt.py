"""
Speech-to-Text — распознавание голосовых сообщений.
"""
import io
import os
import tempfile
import logging

logger = logging.getLogger(__name__)


async def speech_to_text(audio_bytes: bytes, language: str = "en-US") -> str:
    """
    Конвертировать аудио (OGG из Telegram) в текст.
    language: "en-US" для английского, "ru-RU" для русского
    """
    try:
        import speech_recognition as sr
        from pydub import AudioSegment

        with tempfile.TemporaryDirectory() as tmpdir:
            ogg_path = os.path.join(tmpdir, "voice.ogg")
            wav_path = os.path.join(tmpdir, "voice.wav")

            with open(ogg_path, "wb") as f:
                f.write(audio_bytes)

            # OGG → WAV
            audio = AudioSegment.from_ogg(ogg_path)
            audio.export(wav_path, format="wav")

            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                # Убираем фоновый шум
                recognizer.adjust_for_ambient_noise(source, duration=0.3)
                audio_data = recognizer.record(source)

            text = recognizer.recognize_google(audio_data, language=language)
            return text

    except Exception as e:
        logger.warning(f"STT error: {e}")
        return ""
