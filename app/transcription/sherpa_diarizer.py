import logging
import os
import urllib.request
from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

logger = logging.getLogger(__name__)

MODELS_DIR = Path.home() / ".talktrack" / "models" / "diarization"

SEGMENTATION_URL = "https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/main/model.onnx"
EMBEDDING_URL = "https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/main/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"

SEGMENTATION_FILENAME = "segmentation.onnx"
EMBEDDING_FILENAME = "embedding.onnx"


@dataclass
class SpeakerSegment:
    start: float
    end: float
    speaker: str


def get_models_dir() -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return MODELS_DIR


def are_models_available() -> bool:
    models_dir = get_models_dir()
    seg_path = models_dir / SEGMENTATION_FILENAME
    emb_path = models_dir / EMBEDDING_FILENAME
    return (
        seg_path.exists()
        and seg_path.stat().st_size > 100_000
        and emb_path.exists()
        and emb_path.stat().st_size > 1_000_000
    )


def download_models(progress_callback=None):
    """Download ONNX segmentation and embedding models if not already present."""
    models_dir = get_models_dir()
    targets = [
        (SEGMENTATION_FILENAME, SEGMENTATION_URL, "segmentation model (~6 MB)"),
        (EMBEDDING_FILENAME, EMBEDDING_URL, "embedding model (~28 MB)"),
    ]

    for filename, url, desc in targets:
        dest = models_dir / filename
        if dest.exists() and dest.stat().st_size > 100_000:
            continue

        temp_dest = models_dir / f"{filename}.tmp"
        if progress_callback:
            progress_callback(f"Downloading {desc}...")

        def _reporthook(count, block_size, total_size):
            if progress_callback and total_size > 0:
                percent = int(min(100, (count * block_size / total_size) * 100))
                progress_callback(f"Downloading {desc} ({percent}%)...")

        try:
            urllib.request.urlretrieve(url, temp_dest, reporthook=_reporthook)
            if temp_dest.exists() and temp_dest.stat().st_size > 100_000:
                temp_dest.replace(dest)
            else:
                raise RuntimeError(f"Downloaded file for {filename} is incomplete")
        except Exception as e:
            if temp_dest.exists():
                try:
                    temp_dest.unlink()
                except OSError:
                    pass
            logger.error("Failed to download %s: %s", filename, e)
            raise RuntimeError(f"Failed to download {desc}: {e}") from e

    if progress_callback:
        progress_callback("ONNX models ready.")


class SherpaOnnxDiarizer:
    """Speaker diarization engine using sherpa-onnx.

    Runs pure ONNX Runtime models on CPU without requiring PyTorch
    or a HuggingFace account token.
    """

    def __init__(self, num_threads=None):
        import sherpa_onnx

        download_models()

        models_dir = get_models_dir()
        seg_path = str(models_dir / SEGMENTATION_FILENAME)
        emb_path = str(models_dir / EMBEDDING_FILENAME)

        threads = num_threads or max(1, (os.cpu_count() or 4) - 1)

        self.config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=seg_path
                ),
                num_threads=threads,
                provider="cpu",
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=emb_path,
                num_threads=threads,
                provider="cpu",
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=-1,
                threshold=0.5,
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        self.diarizer = sherpa_onnx.OfflineSpeakerDiarization(self.config)

    def diarize(
        self,
        audio_path,
        min_speakers=None,
        max_speakers=None,
        progress_callback=None,
    ) -> list[SpeakerSegment]:
        # If an exact speaker count is specified
        if (
            min_speakers is not None
            and max_speakers is not None
            and min_speakers == max_speakers
            and min_speakers > 0
        ):
            self.config.clustering.num_clusters = min_speakers
            self.diarizer.set_config(self.config)
        elif self.config.clustering.num_clusters != -1:
            self.config.clustering.num_clusters = -1
            self.diarizer.set_config(self.config)

        audio_data, sample_rate = sf.read(audio_path, dtype="float32")
        if audio_data.ndim > 1:
            audio_data = audio_data.mean(axis=1)

        expected_sr = self.diarizer.sample_rate  # 16000
        if sample_rate != expected_sr:
            g = gcd(sample_rate, expected_sr)
            up = expected_sr // g
            down = sample_rate // g
            audio_data = resample_poly(audio_data, up, down).astype(np.float32)

        def _callback(processed, total):
            if progress_callback and total > 0:
                percent = int((processed / total) * 100)
                progress_callback(f"Running speaker diarization ({percent}%)...")
            return 0

        res = self.diarizer.process(audio_data, _callback)
        segments = []
        for seg in res.sort_by_start_time():
            spk_label = f"Speaker {seg.speaker + 1}"
            segments.append(
                SpeakerSegment(start=seg.start, end=seg.end, speaker=spk_label)
            )

        return segments
