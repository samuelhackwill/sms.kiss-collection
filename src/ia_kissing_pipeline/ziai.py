from __future__ import annotations

import json
import pickle
import shutil
import sys
from pathlib import Path
from typing import Callable

from ia_kissing_pipeline.video.extract_clips import extract_clip


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


def run_ziai_pipeline(
    source_path: Path,
    output_dir: Path,
    *,
    min_frames: int = 10,
    threshold: float = 0.7,
    clip_padding_seconds: float = 2.0,
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

    _emit(
        progress_callback,
        "extracting_frames",
        phase="extracting_frames",
        progress=0.05,
        message=f"Extracting roughly one-second audio and video frames from {source_path.name}",
    )
    audio, images = BuildDataset.one_video_extract_audio_and_stills(str(source_path))
    frame_count = len(images)
    _emit(
        progress_callback,
        "frames_extracted",
        phase="classifying_frames",
        progress=0.25,
        message=f"Extracted {frame_count} synchronized 0.96-second frames",
        frame_count=frame_count,
    )

    with model_path.open("rb") as model_file:
        model = pickle.load(model_file)
    model.eval()

    predictions: list[int] = []
    confidences: list[float] = []
    manifest_frames: list[dict[str, object]] = []
    report_every = max(1, frame_count // 50)
    for index, (audio_frame, image_frame) in enumerate(zip(audio, images)):
        with torch.inference_mode():
            logits = model(audio_frame.unsqueeze(0), image_frame.unsqueeze(0))
            probabilities = torch.softmax(logits, dim=1)[0]
            prediction = int(torch.argmax(probabilities).item())
            confidence = float(probabilities[prediction].item())
        frame_path = frames_dir / f"frame_{index + 1:06d}.jpg"
        BuildDataset.transform_reverse(image_frame).save(frame_path, format="JPEG", quality=86)
        predictions.append(prediction)
        confidences.append(confidence)
        manifest_frames.append(
            {
                "index": index,
                "timestamp_seconds": round((index + 1) * ZIAI_FRAME_SECONDS, 3),
                "prediction": prediction,
                "confidence": round(confidence, 6),
                "frame_path": str(frame_path),
            }
        )
        if index == 0 or (index + 1) % report_every == 0 or index + 1 == frame_count:
            progress = 0.25 + (0.5 * (index + 1) / max(1, frame_count))
            _emit(
                progress_callback,
                "frame_classified",
                phase="classifying_frames",
                progress=progress,
                message=f"Classified frame {index + 1} of {frame_count}",
                frame_index=index + 1,
                frame_count=frame_count,
                positive_count=sum(predictions),
            )

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
        start_seconds = max(0.0, indices[0] * ZIAI_FRAME_SECONDS - clip_padding_seconds)
        end_seconds = (indices[-1] + 1) * ZIAI_FRAME_SECONDS + clip_padding_seconds
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
        "frames": manifest_frames,
        "candidates": candidates,
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    return result
