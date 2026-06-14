from __future__ import annotations

import gc
import json
import pickle
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from ia_kissing_pipeline.video.extract_clips import extract_clip
from ia_kissing_pipeline.video.probe import probe_media


ZIAI_FRAME_SECONDS = 0.96
ZiaiProgressCallback = Callable[[str, dict[str, object]], None]


def _detector_root() -> Path:
    return Path(__file__).resolve().parents[2] / "ziai_kissing_detector"


def _emit(callback: ZiaiProgressCallback | None, event_type: str, **payload: object) -> None:
    if callback is not None:
        callback(event_type, payload)


def _candidate_indices(predictions: list[int], min_frames: int, threshold: float) -> list[list[int]]:
    candidates: list[tuple[int, int]] = []
    for start_index, prediction in enumerate(predictions):
        if prediction != 1 or len(predictions) - start_index < min_frames:
            continue
        best: tuple[int, int] | None = None
        for end_index in range(start_index + min_frames - 1, len(predictions)):
            if predictions[end_index] != 1:
                continue
            density = sum(predictions[start_index : end_index + 1]) / (end_index - start_index + 1)
            if density >= threshold:
                best = (start_index, end_index)
        if best is not None:
            candidates.append(best)

    merged: list[tuple[int, int]] = []
    for start_index, end_index in candidates:
        if merged and start_index <= merged[-1][1]:
            if end_index - start_index > merged[-1][1] - merged[-1][0]:
                merged[-1] = (start_index, end_index)
            continue
        merged.append((start_index, end_index))
    return [list(range(start_index, end_index + 1)) for start_index, end_index in merged]


def _chunk_windows(duration_seconds: float, chunk_seconds: float) -> list[tuple[float, float]]:
    windows = []
    start_seconds = 0.0
    while start_seconds < duration_seconds:
        windows.append((start_seconds, min(chunk_seconds, duration_seconds - start_seconds)))
        start_seconds += chunk_seconds
    return windows


def _extract_chunk(source_path: Path, output_path: Path, start_seconds: float, duration_seconds: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_seconds),
            "-i",
            str(source_path),
            "-t",
            str(duration_seconds),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(output_path),
        ],
        text=True,
        capture_output=True,
        check=True,
    )


def run_ziai_pipeline(
    source_path: Path,
    output_dir: Path,
    *,
    min_frames: int = 10,
    threshold: float = 0.7,
    clip_padding_seconds: float = 2.0,
    chunk_seconds: float = 300.0,
    inference_batch_size: int = 8,
    progress_callback: ZiaiProgressCallback | None = None,
) -> dict:
    detector_root = _detector_root()
    model_path = detector_root / "model.pkl"
    weights_path = detector_root / "vggish-pretrained.pth"
    if not model_path.exists():
        raise RuntimeError(f"ZIAI model is missing: {model_path}")
    if not weights_path.exists():
        raise RuntimeError(f"ZIAI VGGish weights are missing: {weights_path}")

    if str(detector_root) not in sys.path:
        sys.path.insert(0, str(detector_root))
    try:
        import torch
        from pipeline import BuildDataset
    except ImportError as exc:
        raise RuntimeError(
            "ZIAI dependencies are unavailable. Install moviepy<2, resampy, soundfile, torch, torchvision, and opencv."
        ) from exc

    shutil.rmtree(output_dir, ignore_errors=True)
    frames_dir = output_dir / "frames"
    candidates_dir = output_dir / "candidates"
    frames_dir.mkdir(parents=True, exist_ok=True)
    candidates_dir.mkdir(parents=True, exist_ok=True)

    duration_seconds = float(probe_media(source_path)["duration_seconds"] or 0.0)
    if duration_seconds <= 0:
        raise RuntimeError(f"Could not determine video duration for {source_path}")
    chunk_windows = _chunk_windows(duration_seconds, max(30.0, chunk_seconds))

    _emit(
        progress_callback,
        "loading_model",
        phase="loading_model",
        progress=0.02,
        message="Loading the ZIAI classifier",
    )
    with model_path.open("rb") as model_file:
        model = pickle.load(model_file)
    model.eval()
    _emit(
        progress_callback,
        "model_loaded",
        phase="loading_model",
        progress=0.04,
        message="Loaded the ZIAI classifier",
    )

    predictions: list[int] = []
    confidences: list[float] = []
    manifest_frames: list[dict[str, object]] = []
    processed_seconds = 0.0
    _emit(
        progress_callback,
        "extracting_frames",
        phase="extracting_frames",
        progress=0.05,
        message=f"Starting {len(chunk_windows)} bounded extraction chunk(s) for {source_path.name}",
        chunk_count=len(chunk_windows),
        duration_seconds=duration_seconds,
    )
    with tempfile.TemporaryDirectory(prefix="ziai-chunks-") as temp_dir_value:
        temp_dir = Path(temp_dir_value)
        for chunk_index, (chunk_start, chunk_duration) in enumerate(chunk_windows, start=1):
            chunk_path = temp_dir / f"chunk_{chunk_index:04d}.mkv"
            chunk_base_progress = 0.05 + (0.7 * processed_seconds / duration_seconds)
            _emit(
                progress_callback,
                "chunk_started",
                phase="extracting_frames",
                progress=chunk_base_progress,
                message=f"Extracting chunk {chunk_index} of {len(chunk_windows)}",
                chunk_index=chunk_index,
                chunk_count=len(chunk_windows),
                chunk_start_seconds=chunk_start,
                chunk_duration_seconds=chunk_duration,
            )
            _extract_chunk(source_path, chunk_path, chunk_start, chunk_duration)
            audio, images = BuildDataset.one_video_extract_audio_and_stills(str(chunk_path))
            chunk_frame_count = len(images)
            _emit(
                progress_callback,
                "chunk_extracted",
                phase="classifying_frames",
                progress=chunk_base_progress,
                message=f"Extracted {chunk_frame_count} frames from chunk {chunk_index} of {len(chunk_windows)}",
                chunk_index=chunk_index,
                chunk_count=len(chunk_windows),
                chunk_frame_count=chunk_frame_count,
            )

            for batch_start in range(0, chunk_frame_count, max(1, inference_batch_size)):
                batch_end = min(chunk_frame_count, batch_start + max(1, inference_batch_size))
                audio_batch = torch.stack(audio[batch_start:batch_end])
                image_batch = torch.stack(images[batch_start:batch_end])
                with torch.inference_mode():
                    probabilities_batch = torch.softmax(model(audio_batch, image_batch), dim=1)
                for batch_offset, probabilities in enumerate(probabilities_batch):
                    local_index = batch_start + batch_offset
                    global_index = len(predictions)
                    prediction = int(torch.argmax(probabilities).item())
                    confidence = float(probabilities[prediction].item())
                    frame_path = frames_dir / f"frame_{global_index + 1:06d}.jpg"
                    BuildDataset.transform_reverse(images[local_index]).save(frame_path, format="JPEG", quality=86)
                    predictions.append(prediction)
                    confidences.append(confidence)
                    manifest_frames.append(
                        {
                            "index": global_index,
                            "timestamp_seconds": round(chunk_start + ((local_index + 1) * ZIAI_FRAME_SECONDS), 3),
                            "prediction": prediction,
                            "confidence": round(confidence, 6),
                            "frame_path": str(frame_path),
                        }
                    )
                chunk_fraction = batch_end / max(1, chunk_frame_count)
                progress = 0.05 + (0.7 * (processed_seconds + (chunk_duration * chunk_fraction)) / duration_seconds)
                _emit(
                    progress_callback,
                    "frames_classified",
                    phase="classifying_frames",
                    progress=progress,
                    message=(
                        f"Classified {batch_end} of {chunk_frame_count} frames "
                        f"in chunk {chunk_index} of {len(chunk_windows)}"
                    ),
                    chunk_index=chunk_index,
                    chunk_count=len(chunk_windows),
                    chunk_frame_index=batch_end,
                    chunk_frame_count=chunk_frame_count,
                    frame_count=len(predictions),
                    positive_count=sum(predictions),
                )
                del audio_batch, image_batch, probabilities_batch
            processed_seconds += chunk_duration
            chunk_path.unlink(missing_ok=True)
            del audio, images
            gc.collect()
            _emit(
                progress_callback,
                "chunk_complete",
                phase="extracting_frames",
                progress=0.05 + (0.7 * processed_seconds / duration_seconds),
                message=f"Completed chunk {chunk_index} of {len(chunk_windows)}",
                chunk_index=chunk_index,
                chunk_count=len(chunk_windows),
                frame_count=len(predictions),
                positive_count=sum(predictions),
            )

    frame_count = len(predictions)
    segments = _candidate_indices(predictions, min_frames, threshold)
    _emit(
        progress_callback,
        "candidates_found",
        phase="building_candidate_clips",
        progress=0.78,
        message=f"Found {len(segments)} likely kissing sequence candidate(s)",
        candidate_count=len(segments),
    )
    candidates: list[dict[str, object]] = []
    for candidate_index, indices in enumerate(segments, start=1):
        start_seconds = max(
            0.0,
            float(manifest_frames[indices[0]]["timestamp_seconds"]) - ZIAI_FRAME_SECONDS - clip_padding_seconds,
        )
        end_seconds = float(manifest_frames[indices[-1]]["timestamp_seconds"]) + clip_padding_seconds
        clip_path = candidates_dir / f"candidate_{candidate_index:03d}.mp4"
        extract_clip(source_path, clip_path, start_seconds, end_seconds - start_seconds)
        positive_confidences = [
            confidences[index]
            for index in indices
            if predictions[index] == 1
        ]
        candidates.append(
            {
                "candidate_index": candidate_index,
                "start_seconds": round(start_seconds, 3),
                "end_seconds": round(end_seconds, 3),
                "confidence": round(sum(positive_confidences) / max(1, len(positive_confidences)), 6),
                "positive_frames": sum(predictions[index] for index in indices),
                "total_frames": len(indices),
                "clip_path": str(clip_path),
            }
        )
        _emit(
            progress_callback,
            "candidate_clip_built",
            phase="building_candidate_clips",
            progress=0.78 + (0.2 * candidate_index / max(1, len(segments))),
            message=f"Built candidate clip {candidate_index} of {len(segments)}",
            candidate_index=candidate_index,
            candidate_count=len(segments),
        )

    result = {
        "source_path": str(source_path),
        "output_dir": str(output_dir),
        "frame_seconds": ZIAI_FRAME_SECONDS,
        "frame_count": frame_count,
        "positive_frame_count": sum(predictions),
        "candidate_count": len(candidates),
        "min_frames": min_frames,
        "threshold": threshold,
        "clip_padding_seconds": clip_padding_seconds,
        "chunk_seconds": chunk_seconds,
        "inference_batch_size": inference_batch_size,
        "frames": manifest_frames,
        "candidates": candidates,
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    return result
