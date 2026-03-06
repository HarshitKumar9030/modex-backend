"""
Audio processing service.

Operations:
  - compress_audio       : Reduce audio file size (lower bitrate)
  - convert_audio        : Convert between formats (mp3, wav, ogg, flac, aac, m4a)
  - trim_audio           : Trim audio to a time range
  - adjust_audio_volume  : Increase or decrease volume
  - transcribe_audio     : Transcribe audio to text using Gemini
"""

import os
import logging
from typing import Dict, Any

from pydub import AudioSegment

from core.config import settings

logger = logging.getLogger(__name__)

# Supported formats and their pydub export names
FORMAT_MAP = {
    "mp3": "mp3",
    "wav": "wav",
    "ogg": "ogg",
    "flac": "flac",
    "aac": "adts",   # pydub uses "adts" for raw AAC
    "m4a": "mp4",    # m4a is mp4 container with audio
}


class AudioService:

    # ── Compress ──────────────────────────────────────────────────

    @staticmethod
    async def compress_audio(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Compress audio by lowering bitrate. Params:
          - target_size_kb (int, optional): target file size in KB
          - bitrate (str, optional): e.g. "128k", "64k", "32k"
          - format (str, optional): output format, default "mp3"
        """
        target_kb = params.get("target_size_kb")
        bitrate = params.get("bitrate", "128k")
        fmt = params.get("format", "mp3").lower()

        try:
            audio = AudioSegment.from_file(input_path)
            export_format = FORMAT_MAP.get(fmt, "mp3")

            # Fix output extension
            base = os.path.splitext(output_path)[0]
            output_path = f"{base}.{fmt}"

            if target_kb:
                # Calculate required bitrate for target size
                duration_sec = len(audio) / 1000.0
                target_bits = target_kb * 1024 * 8
                required_bitrate = int(target_bits / duration_sec)
                # Clamp to reasonable range
                required_bitrate = max(8000, min(required_bitrate, 320000))
                bitrate = f"{required_bitrate // 1000}k"

            audio.export(output_path, format=export_format, bitrate=bitrate)
            final_size = os.path.getsize(output_path) / 1024
            return f"Compressed audio to {final_size:.1f} KB (bitrate: {bitrate})"

        except Exception as e:
            logger.error(f"Audio compress failed: {e}")
            raise ValueError(f"Failed to compress audio: {e}")

    # ── Convert ───────────────────────────────────────────────────

    @staticmethod
    async def convert_audio(input_path: str, output_path: str, params: Dict[str, Any]) -> tuple[str, str]:
        """
        Convert audio format. Params:
          - format (str): target format, e.g. "mp3", "wav", "ogg", "flac"
          - bitrate (str, optional): e.g. "192k"

        Returns:
          (message, actual_output_path)
        """
        target_format = params.get("format", "mp3").lower()
        bitrate = params.get("bitrate", "192k")

        try:
            audio = AudioSegment.from_file(input_path)
            export_format = FORMAT_MAP.get(target_format)
            if not export_format:
                raise ValueError(f"Unsupported audio format: {target_format}")

            base = os.path.splitext(output_path)[0]
            output_path = f"{base}.{target_format}"

            export_params = {"format": export_format}
            if target_format not in ("wav", "flac"):  # lossless don't need bitrate
                export_params["bitrate"] = bitrate

            audio.export(output_path, **export_params)
            final_size = os.path.getsize(output_path) / 1024
            return f"Converted to {target_format.upper()} ({final_size:.1f} KB)", output_path

        except Exception as e:
            logger.error(f"Audio convert failed: {e}")
            raise ValueError(f"Failed to convert audio: {e}")

    # ── Trim ──────────────────────────────────────────────────────

    @staticmethod
    async def trim_audio(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Trim audio to a time range. Params:
          - start_ms (int, optional): start time in milliseconds, default 0
          - end_ms (int, optional): end time in milliseconds, default end
          - start_sec (float, optional): start in seconds (alternative)
          - end_sec (float, optional): end in seconds (alternative)
        """
        try:
            audio = AudioSegment.from_file(input_path)

            # Accept both ms and seconds
            start = params.get("start_ms", 0)
            end = params.get("end_ms", len(audio))

            if "start_sec" in params:
                start = int(params["start_sec"] * 1000)
            if "end_sec" in params:
                end = int(params["end_sec"] * 1000)

            trimmed = audio[start:end]

            # Keep same format
            ext = os.path.splitext(input_path)[1].lstrip(".").lower()
            export_format = FORMAT_MAP.get(ext, "mp3")
            base = os.path.splitext(output_path)[0]
            output_path = f"{base}.{ext}"

            trimmed.export(output_path, format=export_format)
            duration = len(trimmed) / 1000.0
            return f"Trimmed audio to {duration:.1f}s ({os.path.getsize(output_path) / 1024:.1f} KB)"

        except Exception as e:
            logger.error(f"Audio trim failed: {e}")
            raise ValueError(f"Failed to trim audio: {e}")

    # ── Volume ────────────────────────────────────────────────────

    @staticmethod
    async def adjust_audio_volume(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Adjust volume. Params:
          - change_db (float): decibels to change, e.g. +6 or -3
          - normalize (bool, optional): if True, normalize to 0dBFS
        """
        try:
            audio = AudioSegment.from_file(input_path)

            if params.get("normalize"):
                change = -audio.dBFS  # normalize to 0
                audio = audio.apply_gain(change)
                desc = "Normalized audio"
            else:
                change_db = params.get("change_db", 0)
                audio = audio.apply_gain(change_db)
                desc = f"Adjusted volume by {change_db:+.1f} dB"

            ext = os.path.splitext(input_path)[1].lstrip(".").lower()
            export_format = FORMAT_MAP.get(ext, "mp3")
            base = os.path.splitext(output_path)[0]
            output_path = f"{base}.{ext}"

            audio.export(output_path, format=export_format)
            return desc

        except Exception as e:
            logger.error(f"Audio volume adjust failed: {e}")
            raise ValueError(f"Failed to adjust audio volume: {e}")

    # ── Transcribe ────────────────────────────────────────────────

    @staticmethod
    async def transcribe_audio(input_path: str, output_path: str, params: Dict[str, Any]) -> str:
        """
        Transcribe audio to text using Gemini multimodal. Params:
          - language (str, optional): hint language, default "auto"
          - timestamps (bool, optional): include timestamps, default False
        Saves transcript to a .txt file and returns the text.
        """
        import asyncio
        from google import genai
        from core.config import settings

        language = params.get("language", "auto")
        timestamps = params.get("timestamps", False)

        try:
            # Read audio file bytes
            with open(input_path, "rb") as f:
                audio_bytes = f.read()

            ext = os.path.splitext(input_path)[1].lstrip(".").lower()
            mime_map = {
                "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
                "flac": "audio/flac", "aac": "audio/aac", "m4a": "audio/mp4",
            }
            mime_type = mime_map.get(ext, "audio/mpeg")

            prompt = "Transcribe this audio recording accurately. Return only the transcription text."
            if language != "auto":
                prompt += f" The audio is in {language}."
            if timestamps:
                prompt += " Include timestamps at the start of each segment in [MM:SS] format."

            client = genai.Client(api_key=settings.GEMINI_API_KEY)

            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model="gemini-2.5-flash",
                    contents=[
                        {
                            "parts": [
                                {"inline_data": {"mime_type": mime_type, "data": audio_bytes}},
                                {"text": prompt},
                            ]
                        }
                    ],
                ),
                timeout=120,
            )

            transcript = response.text.strip() if response.text else ""
            if not transcript:
                raise ValueError("Gemini returned an empty transcription")

            # Save transcript to file
            base = os.path.splitext(output_path)[0]
            txt_path = f"{base}.txt"
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(transcript)

            word_count = len(transcript.split())
            return f"Transcribed audio — {word_count} words saved to text file"

        except ImportError:
            raise ValueError("Transcription is not available — google-genai is not installed")
        except TimeoutError:
            raise ValueError("Transcription timed out — the audio file may be too long")
        except Exception as e:
            logger.error(f"Audio transcription failed: {e}")
            raise ValueError(f"Failed to transcribe audio: {e}")
