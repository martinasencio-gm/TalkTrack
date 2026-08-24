"""Sherpa-ONNX Speech-to-Text transcription engine.

Provides standalone, pure ONNX offline speech recognition using Whisper or Moonshine
models without requiring PyTorch or CTranslate2. Supports DirectML for hardware
acceleration on any Windows GPU (Intel, AMD, NVIDIA, Qualcomm).
"""

import logging
import os
import urllib.request
from math import gcd
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from app.transcription.transcriber import TranscriptResult, TranscriptSegment

logger = logging.getLogger(__name__)

MODELS_BASE_DIR = Path.home() / ".talktrack" / "models" / "transcription"

# Model definition registry
MODEL_REGISTRY = {
    "tiny.en": {
        "type": "whisper",
        "repo": "https://huggingface.co/csukuangfj/sherpa-onnx-whisper-tiny.en/resolve/main",
        "files": {
            "encoder": "tiny.en-encoder.int8.onnx",
            "decoder": "tiny.en-decoder.int8.onnx",
            "tokens": "tiny.en-tokens.txt",
        },
        "description": "Whisper Tiny (English, ~40 MB)",
    },
    "tiny": {
        "type": "whisper",
        "repo": "https://huggingface.co/csukuangfj/sherpa-onnx-whisper-tiny/resolve/main",
        "files": {
            "encoder": "tiny-encoder.int8.onnx",
            "decoder": "tiny-decoder.int8.onnx",
            "tokens": "tiny-tokens.txt",
        },
        "description": "Whisper Tiny (Multilingual, ~40 MB)",
    },
    "base.en": {
        "type": "whisper",
        "repo": "https://huggingface.co/csukuangfj/sherpa-onnx-whisper-base.en/resolve/main",
        "files": {
            "encoder": "base.en-encoder.int8.onnx",
            "decoder": "base.en-decoder.int8.onnx",
            "tokens": "base.en-tokens.txt",
        },
        "description": "Whisper Base (English, ~75 MB)",
    },
    "base": {
        "type": "whisper",
        "repo": "https://huggingface.co/csukuangfj/sherpa-onnx-whisper-base/resolve/main",
        "files": {
            "encoder": "base-encoder.int8.onnx",
            "decoder": "base-decoder.int8.onnx",
            "tokens": "base-tokens.txt",
        },
        "description": "Whisper Base (Multilingual, ~75 MB)",
    },
    "small.en": {
        "type": "whisper",
        "repo": "https://huggingface.co/csukuangfj/sherpa-onnx-whisper-small.en/resolve/main",
        "files": {
            "encoder": "small.en-encoder.int8.onnx",
            "decoder": "small.en-decoder.int8.onnx",
            "tokens": "small.en-tokens.txt",
        },
        "description": "Whisper Small (English, ~250 MB)",
    },
    "small": {
        "type": "whisper",
        "repo": "https://huggingface.co/csukuangfj/sherpa-onnx-whisper-small/resolve/main",
        "files": {
            "encoder": "small-encoder.int8.onnx",
            "decoder": "small-decoder.int8.onnx",
            "tokens": "small-tokens.txt",
        },
        "description": "Whisper Small (Multilingual, ~250 MB)",
    },
    "moonshine-tiny": {
        "type": "moonshine",
        "repo": "https://huggingface.co/csukuangfj/sherpa-onnx-moonshine-tiny-en-int8/resolve/main",
        "files": {
            "preprocessor": "preprocess.onnx",
            "encoder": "encode.int8.onnx",
            "uncached_decoder": "uncached_decode.int8.onnx",
            "cached_decoder": "cached_decode.int8.onnx",
            "tokens": "tokens.txt",
        },
        "description": "Moonshine Tiny (English, ~30 MB)",
    },
    "moonshine-base": {
        "type": "moonshine",
        "repo": "https://huggingface.co/csukuangfj/sherpa-onnx-moonshine-base-en-int8/resolve/main",
        "files": {
            "preprocessor": "preprocess.onnx",
            "encoder": "encode.int8.onnx",
            "uncached_decoder": "uncached_decode.int8.onnx",
            "cached_decoder": "cached_decode.int8.onnx",
            "tokens": "tokens.txt",
        },
        "description": "Moonshine Base (English, ~60 MB)",
    },
}


VAD_BASE_DIR = Path.home() / ".talktrack" / "models" / "vad"
VAD_FILENAME = "silero_vad.onnx"
VAD_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"


def get_vad_model_path() -> Path:
    VAD_BASE_DIR.mkdir(parents=True, exist_ok=True)
    return VAD_BASE_DIR / VAD_FILENAME


def is_vad_available() -> bool:
    vad_path = get_vad_model_path()
    return vad_path.exists() and vad_path.stat().st_size > 10000


def ensure_vad_model(progress_callback: Optional[Callable[[str], None]] = None) -> Path:
    vad_path = get_vad_model_path()
    if vad_path.exists() and vad_path.stat().st_size > 10000:
        return vad_path

    if progress_callback:
        progress_callback("Downloading Silero VAD model (~0.6 MB)...")

    temp_dest = VAD_BASE_DIR / f"{VAD_FILENAME}.tmp"
    try:
        urllib.request.urlretrieve(VAD_URL, temp_dest)
        if temp_dest.exists():
            temp_dest.replace(vad_path)
    except Exception as e:
        if temp_dest.exists():
            temp_dest.unlink(missing_ok=True)
        # Try HuggingFace fallback mirror
        hf_url = "https://huggingface.co/csukuangfj/silero-vad-v4/resolve/main/silero_vad.onnx"
        urllib.request.urlretrieve(hf_url, vad_path)

    return vad_path


def get_model_dir(model_name: str) -> Path:
    target_dir = MODELS_BASE_DIR / model_name
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def is_model_available(model_name: str) -> bool:
    normalized = model_name.lower().strip()
    if normalized not in MODEL_REGISTRY:
        normalized = "base"
    spec = MODEL_REGISTRY[normalized]
    model_dir = get_model_dir(normalized)
    for key, filename in spec["files"].items():
        file_path = model_dir / filename
        if not file_path.exists():
            return False
        try:
            if file_path.stat().st_size < 1000:
                return False
        except OSError:
            return False
    return True


def download_model(model_name: str, progress_callback: Optional[Callable[[str], None]] = None) -> Path:
    normalized = model_name.lower().strip()
    if normalized not in MODEL_REGISTRY:
        normalized = "base"
    spec = MODEL_REGISTRY[normalized]
    model_dir = get_model_dir(normalized)

    for key, filename in spec["files"].items():
        dest = model_dir / filename
        if dest.exists() and dest.stat().st_size > 1000:
            continue

        url = f"{spec['repo']}/{filename}"
        temp_dest = model_dir / f"{filename}.tmp"
        desc = f"{spec['description']} ({filename})"

        if progress_callback:
            progress_callback(f"Downloading {desc}...")

        def _reporthook(count, block_size, total_size):
            if progress_callback and total_size > 0:
                percent = int(min(100, (count * block_size / total_size) * 100))
                progress_callback(f"Downloading {desc} ({percent}%)...")

        try:
            urllib.request.urlretrieve(url, temp_dest, reporthook=_reporthook)
            if temp_dest.exists():
                temp_dest.replace(dest)
        except Exception as e:
            if temp_dest.exists():
                temp_dest.unlink(missing_ok=True)
            raise RuntimeError(f"Failed to download {desc}: {e}") from e

    return model_dir


class SherpaOnnxTranscriber:
    """Offline Speech-to-Text transcriber using sherpa-onnx."""

    def __init__(
        self,
        model_name: str = "base",
        language: Optional[str] = None,
        num_threads: int = 4,
        provider: str = "cpu",
    ):
        self.model_name = model_name.lower().strip() if model_name else "base"
        if self.model_name not in MODEL_REGISTRY:
            self.model_name = "base"
        self.language = language or "en"
        self.num_threads = max(1, num_threads)
        self.provider = provider or "cpu"
        self._recognizer = None

    def _load_recognizer(self, progress_callback: Optional[Callable[[str], None]] = None):
        import sherpa_onnx

        if not is_model_available(self.model_name):
            download_model(self.model_name, progress_callback=progress_callback)

        spec = MODEL_REGISTRY[self.model_name]
        model_dir = get_model_dir(self.model_name)

        if progress_callback:
            progress_callback("Initializing ONNX Speech Recognizer...")

        if spec["type"] == "whisper":
            encoder = str(model_dir / spec["files"]["encoder"])
            decoder = str(model_dir / spec["files"]["decoder"])
            tokens = str(model_dir / spec["files"]["tokens"])
            lang = self.language if self.language and self.language != "auto" else "en"
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                encoder=encoder,
                decoder=decoder,
                tokens=tokens,
                language=lang,
                task="transcribe",
                num_threads=self.num_threads,
                provider=self.provider,
                enable_segment_timestamps=True,
            )
        elif spec["type"] == "moonshine":
            preprocessor = str(model_dir / spec["files"]["preprocessor"])
            encoder = str(model_dir / spec["files"]["encoder"])
            uncached_decoder = str(model_dir / spec["files"]["uncached_decoder"])
            cached_decoder = str(model_dir / spec["files"]["cached_decoder"])
            tokens = str(model_dir / spec["files"]["tokens"])
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine(
                preprocessor=preprocessor,
                encoder=encoder,
                uncached_decoder=uncached_decoder,
                cached_decoder=cached_decoder,
                tokens=tokens,
                num_threads=self.num_threads,
                provider=self.provider,
            )
        else:
            raise ValueError(f"Unsupported model type: {spec['type']}")

    def _get_speech_segments(
        self,
        audio_data: np.ndarray,
        sample_rate: int = 16000,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> list[tuple[int, np.ndarray]]:
        """Detect speech segments using Silero VAD or fall back to fixed 20-second chunking."""
        import sherpa_onnx

        speech_segments = []
        try:
            vad_path = ensure_vad_model(progress_callback=progress_callback)
            config = sherpa_onnx.VadModelConfig()
            config.silero_vad.model = str(vad_path)
            config.silero_vad.min_silence_duration = 0.25
            config.silero_vad.min_speech_duration = 0.25
            config.silero_vad.max_speech_duration = 20.0
            config.sample_rate = sample_rate
            vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)

            window_size = 512
            for i in range(0, len(audio_data), window_size):
                chunk = audio_data[i:i + window_size]
                vad.accept_waveform(chunk)
            vad.flush()

            while not vad.empty():
                seg = vad.front
                if len(seg.samples) > 0:
                    speech_segments.append((int(seg.start), np.array(seg.samples, dtype=np.float32)))
                vad.pop()
        except Exception as e:
            logger.warning("VAD segmentation failed (%s), falling back to chunking", e)

        # Fallback to chunking if VAD yielded no segments but audio is present
        if not speech_segments and len(audio_data) > 0:
            chunk_size = 20 * sample_rate
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                if len(chunk) > 0:
                    speech_segments.append((i, chunk))

        return speech_segments

    def transcribe(
        self,
        audio_path: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Optional[tuple[list[TranscriptSegment], dict]]:
        """Transcribe an audio file of any length and return (segments, info) or None if cancelled."""
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if self._recognizer is None:
            def _cb(msg):
                if progress_callback:
                    progress_callback(0, msg)
            self._load_recognizer(progress_callback=_cb)

        if is_cancelled and is_cancelled():
            return None

        # Load and convert audio to mono 16000Hz float32
        audio_data, sample_rate = sf.read(audio_path, dtype="float32")
        if audio_data.ndim > 1:
            audio_data = audio_data.mean(axis=1)

        if sample_rate != 16000:
            g = gcd(16000, sample_rate)
            up = 16000 // g
            down = sample_rate // g
            audio_data = resample_poly(audio_data, up, down).astype(np.float32)
            sample_rate = 16000

        total_samples = len(audio_data)
        duration = total_samples / sample_rate

        if is_cancelled and is_cancelled():
            return None

        def _vad_cb(msg):
            if progress_callback:
                progress_callback(5, msg)

        speech_segments = self._get_speech_segments(
            audio_data,
            sample_rate=16000,
            progress_callback=_vad_cb,
        )

        if is_cancelled and is_cancelled():
            return None

        total_segs = len(speech_segments)
        total_speech_samples = sum(len(samples) for _, samples in speech_segments)
        processed_samples = 0
        segments = []
        detected_lang = self.language

        for idx, (start_sample, samples) in enumerate(speech_segments):
            if is_cancelled and is_cancelled():
                return None

            start_sec = start_sample / 16000.0
            dur_sec = len(samples) / 16000.0
            end_sec = start_sec + dur_sec

            stream = self._recognizer.create_stream()
            stream.accept_waveform(sample_rate, samples)
            self._recognizer.decode_stream(stream)
            res = stream.result

            processed_samples += len(samples)
            if progress_callback and total_speech_samples > 0:
                pct = int(min(99, (processed_samples / total_speech_samples) * 100))
                progress_callback(pct, f"Transcribing audio with ONNX ({idx + 1}/{total_segs})...")

            if getattr(res, "lang", None):
                detected_lang = res.lang

            # If model outputs subsegment-level timestamps:
            if getattr(res, "segment_timestamps", None) and len(res.segment_timestamps) > 0:
                texts = getattr(res, "segment_texts", [])
                starts = res.segment_timestamps
                durations = getattr(res, "segment_durations", [])
                for i, s in enumerate(starts):
                    d = durations[i] if i < len(durations) else (dur_sec - s)
                    t = texts[i].strip() if i < len(texts) else ""
                    if t:
                        segments.append(TranscriptSegment(
                            start=round(float(start_sec + s), 2),
                            end=round(float(start_sec + s + d), 2),
                            text=t,
                            confidence=1.0,
                        ))
            elif getattr(res, "text", None) and res.text.strip():
                segments.append(TranscriptSegment(
                    start=round(start_sec, 2),
                    end=round(end_sec, 2),
                    text=res.text.strip(),
                    confidence=1.0,
                ))

        info = {
            "language": detected_lang or self.language,
            "duration": duration,
        }

        if progress_callback:
            progress_callback(100, "Transcription finished.")

        return segments, info
