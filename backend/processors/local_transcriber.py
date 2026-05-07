"""Local Gemma 4 E4B transcription service.

Runs google/gemma-4-E4B-it on-device (MPS on Apple Silicon, CPU elsewhere)
to transcribe journalist audio without sending audio bytes to external APIs.

Singleton pattern: the model is loaded once on first call to get_local_transcriber()
and kept in memory for the lifetime of the process. Startup loading is triggered
from the FastAPI lifespan so the first transcription request is not cold.

If the model cannot be loaded (missing weights, OOM, etc.) TranscriptionUnavailableError
is raised — the route layer converts this to HTTP 503. There is no Gemini API fallback;
local-only transcription is an explicit design requirement.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from threading import Lock

import librosa
import numpy as np
import torch
from loguru import logger
from transformers import AutoModelForMultimodalLM, AutoProcessor

_MODEL_ID = "google/gemma-4-E4B-it"

_LANG_MAP: dict[str, str] = {
    "en": "English",
    "ar": "Arabic",
    "fr": "French",
    "tr": "Turkish",
    "es": "Spanish",
}

_ASR_PROMPT = (
    "Transcribe the following speech segment in {LANGUAGE} into {LANGUAGE} text. "
    "Follow these specific instructions for formatting the answer: "
    "* Only output the transcription, with no newlines. "
    "* When transcribing numbers, write the digits, i.e. write 1.7 and not one point seven, "
    "and write 3 instead of three."
)


class TranscriptionUnavailableError(RuntimeError):
    """Raised when the local model is not loaded or audio preprocessing fails."""


def _detect_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _audio_to_array(audio_bytes: bytes, mime_type: str) -> np.ndarray:
    """Convert raw audio bytes → 16 kHz mono float32 numpy array via ffmpeg + librosa."""
    suffix = _mime_to_suffix(mime_type)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src_f:
        src_f.write(audio_bytes)
        src_path = src_f.name

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as dst_f:
        dst_path = dst_f.name

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y", "-v", "error",
                "-i", src_path,
                "-ar", "16000",
                "-ac", "1",
                "-f", "wav",
                dst_path,
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            ffmpeg_stderr = result.stderr.decode(errors="replace")
            logger.error(f"local_transcriber: ffmpeg failed (rc={result.returncode}): {ffmpeg_stderr}")
            raise TranscriptionUnavailableError(
                f"ffmpeg conversion failed (rc={result.returncode}): {ffmpeg_stderr[:400]}"
            )
        wav_size = Path(dst_path).stat().st_size
        logger.debug(f"local_transcriber: WAV size={wav_size}B src_suffix={Path(src_path).suffix!r}")
        audio_array, _ = librosa.load(dst_path, sr=16000, mono=True)
        return audio_array.astype(np.float32)
    finally:
        Path(src_path).unlink(missing_ok=True)
        Path(dst_path).unlink(missing_ok=True)


def _mime_to_suffix(mime_type: str) -> str:
    mapping = {
        "audio/wav": ".wav",
        "audio/wave": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
        "audio/mp4": ".m4a",
        "audio/m4a": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/ogg": ".ogg",
        "audio/aac": ".aac",
        "audio/x-aac": ".aac",
    }
    return mapping.get(mime_type.lower().split(";")[0].strip(), ".m4a")


class LocalTranscriber:
    """Singleton wrapper around Gemma 4 E4B for on-device ASR."""

    def __init__(self) -> None:
        device = _detect_device()
        logger.info(f"local_transcriber: loading {_MODEL_ID!r} on device={device!r}")
        self._processor = AutoProcessor.from_pretrained(_MODEL_ID)
        self._model = AutoModelForMultimodalLM.from_pretrained(
            _MODEL_ID,
            device_map={"": device},
            torch_dtype=torch.bfloat16,
        )
        self._model.eval()
        logger.info("local_transcriber: model ready")

    def transcribe(self, audio_bytes: bytes, mime_type: str, language: str = "en") -> str:
        """Transcribe *audio_bytes* and return the text.

        Raises:
            TranscriptionUnavailableError: if audio preprocessing or inference fails.
        """
        lang_name = _LANG_MAP.get(language, "English")
        prompt_text = _ASR_PROMPT.format(LANGUAGE=lang_name)

        audio_array = _audio_to_array(audio_bytes, mime_type)
        logger.debug(
            f"local_transcriber: audio array shape={audio_array.shape}"
            f" dtype={audio_array.dtype} min={audio_array.min():.3f} max={audio_array.max():.3f}"
        )
        if audio_array.shape[0] < 8000:
            logger.warning(
                f"local_transcriber: audio too short to transcribe"
                f" ({audio_array.shape[0]} samples, need ≥8000)"
            )
            return ""

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": audio_array},
                    {"type": "text", "text": prompt_text},
                ],
            }
        ]

        inputs = self._processor.apply_chat_template(
            messages,
            tokenize=True,
            return_tensors="pt",
            return_dict=True,
            add_generation_prompt=True,
        )
        inputs = {k: v.to(self._model.device) for k, v in inputs.items()}
        input_length = inputs["input_ids"].shape[1]

        with torch.no_grad():
            output_ids = self._model.generate(**inputs, max_new_tokens=500)

        # Slice off prompt tokens — skip_prompt in TextIteratorStreamer is unreliable
        # for multimodal inputs where audio tokens aren't counted as input_ids length.
        generated_ids = output_ids[:, input_length:]
        raw = self._processor.tokenizer.decode(generated_ids[0], skip_special_tokens=True)
        logger.debug(f"local_transcriber: raw output={raw!r}")
        text = raw.strip()
        logger.debug(f"local_transcriber: transcribed text={text!r}")
        return text


_instance: LocalTranscriber | None = None
_init_lock = Lock()


def get_local_transcriber() -> LocalTranscriber:
    """Return the singleton LocalTranscriber, initialising it on first call."""
    global _instance
    if _instance is not None:
        return _instance
    with _init_lock:
        if _instance is None:
            _instance = LocalTranscriber()
    return _instance
