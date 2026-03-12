from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel


def _serialize_segments(segments: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        serialized.append(
            {
                "index": index,
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "duration": round(float(segment.end - segment.start), 3),
                "text": segment.text.strip(),
                "words": [
                    {
                        "start": round(float(word.start), 3),
                        "end": round(float(word.end), 3),
                        "duration": round(float(word.end - word.start), 3),
                        "word": word.word,
                        "probability": round(float(word.probability), 4) if word.probability is not None else None,
                    }
                    for word in (segment.words or [])
                    if word.start is not None and word.end is not None
                ],
            }
        )
    return serialized


def main() -> int:
    parser = argparse.ArgumentParser(description="Run precise timestamped transcription with faster-whisper.")
    parser.add_argument("--audio", required=True, help="Audio file path.")
    parser.add_argument("--output", required=True, help="JSON output path.")
    parser.add_argument("--model", default="large-v3-turbo", help="Whisper model name.")
    parser.add_argument("--language", default="zh", help="Language code.")
    parser.add_argument("--compute-type", default="int8", help="CTranslate2 compute type.")
    parser.add_argument("--device", default="cpu", help="Inference device.")
    parser.add_argument("--min-silence-ms", type=int, default=300, help="VAD minimum silence duration.")
    parser.add_argument("--initial-prompt", default="", help="Optional prompt to bias recognition.")
    args = parser.parse_args()

    audio_path = Path(args.audio).resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=args.language,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": args.min_silence_ms},
        word_timestamps=True,
        initial_prompt=args.initial_prompt or None,
    )
    segments = list(segments_iter)

    payload = {
        "audio_path": str(audio_path),
        "model": args.model,
        "language": info.language,
        "duration": round(float(info.duration), 3) if info.duration is not None else None,
        "duration_after_vad": round(float(info.duration_after_vad), 3) if info.duration_after_vad is not None else None,
        "segments": _serialize_segments(segments),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
