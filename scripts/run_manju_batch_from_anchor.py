"""Run a Manju multi-shot batch using an existing anchor image."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.cli import (
    _build_manju_video_prompt,
    _estimate_manju_duration_seconds,
    _load_dotenv,
    _resolve_catalog_asset_image,
    _resolve_catalog_path,
    _run_manju_one_shot_script,
    get_config,
)


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return (project_root / candidate).resolve()


def _verify_video(video_path: Path) -> tuple[bool, str]:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(video_path),
        "-f",
        "null",
        "-",
    ]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return video_path.exists() and video_path.stat().st_size > 0, "ffmpeg_not_found"
    return process.returncode == 0, process.stderr.strip()


def _normalize_shots(payload: dict[str, object], project_root: Path) -> list[dict[str, object]]:
    shots = payload.get("shots") or payload.get("storyboards") or []
    if not isinstance(shots, list) or not shots:
        raise ValueError("batch payload is missing shots")

    defaults = {
        "character_ref": payload.get("character_ref", ""),
        "scene_ref": payload.get("scene_ref", ""),
        "video_prompt": payload.get("video_prompt", ""),
        "aspect_ratio": payload.get("aspect_ratio", "16:9"),
        "duration_seconds": int(payload.get("duration_seconds") or 0),
        "manju_mode": payload.get("manju_mode", "普通模式"),
        "manju_resolution": payload.get("manju_resolution", "1080p"),
        "manju_model_name": payload.get("manju_model_name", "Seedance1.5-pro"),
        "manju_profile_dir": payload.get("manju_profile_dir", ""),
        "manju_project_url": payload.get("manju_project_url", ""),
        "manju_headless": bool(payload.get("manju_headless", True)),
        "anchor_image_path": payload.get("anchor_image_path", ""),
        "skip_video_audit": bool(payload.get("skip_video_audit", True)),
    }

    normalized: list[dict[str, object]] = []
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            raise ValueError(f"shot {index} is not an object")
        storyboard_id = str(shot.get("storyboard_id") or shot.get("id") or f"manju_batch_{index:03d}")
        storyboard_text = str(shot.get("storyboard_text") or shot.get("summary") or "").strip()
        character_ref = str(shot.get("character_ref") or defaults["character_ref"] or "").strip()
        scene_ref = str(shot.get("scene_ref") or defaults["scene_ref"] or "").strip()
        if not storyboard_text:
            raise ValueError(f"shot {storyboard_id} is missing storyboard_text")
        if not character_ref or not scene_ref:
            raise ValueError(f"shot {storyboard_id} is missing character_ref or scene_ref")
        anchor_image_path = str(shot.get("anchor_image_path") or defaults["anchor_image_path"] or "").strip()
        if not anchor_image_path:
            raise ValueError(f"shot {storyboard_id} is missing anchor_image_path")
        normalized.append(
            {
                "storyboard_id": storyboard_id,
                "storyboard_text": storyboard_text,
                "character_ref": character_ref,
                "scene_ref": scene_ref,
                "video_prompt": str(shot.get("video_prompt") or defaults["video_prompt"] or ""),
                "aspect_ratio": str(shot.get("aspect_ratio") or defaults["aspect_ratio"] or "16:9"),
                "duration_seconds": int(shot.get("duration_seconds") or defaults["duration_seconds"] or 0),
                "manju_mode": str(shot.get("manju_mode") or defaults["manju_mode"] or "普通模式"),
                "manju_resolution": str(shot.get("manju_resolution") or defaults["manju_resolution"] or "1080p"),
                "manju_model_name": str(
                    shot.get("manju_model_name") or defaults["manju_model_name"] or "Seedance1.5-pro"
                ),
                "manju_profile_dir": str(shot.get("manju_profile_dir") or defaults["manju_profile_dir"] or ""),
                "manju_project_url": str(shot.get("manju_project_url") or defaults["manju_project_url"] or ""),
                "manju_headless": bool(shot.get("manju_headless", defaults["manju_headless"])),
                "anchor_image_path": _resolve_path(project_root, anchor_image_path),
                "skip_video_audit": bool(shot.get("skip_video_audit", defaults["skip_video_audit"])),
                "video_output_path": _resolve_path(
                    project_root,
                    str(shot.get("video_output_path") or f"outputs/videos/{storyboard_id}.mp4"),
                ),
                "report_path": _resolve_path(
                    project_root,
                    str(shot.get("report_path") or f"outputs/reviews/{storyboard_id}_batch_report.json"),
                ),
            }
        )
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Manju multi-shot batch with an existing anchor image.")
    parser.add_argument("--task-file", required=True, help="Batch task JSON path")
    args = parser.parse_args()

    _load_dotenv()
    config = get_config()
    project_root = config.project_root
    task_path = _resolve_path(project_root, args.task_file)
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    shots = _normalize_shots(payload, project_root)
    catalog_path = _resolve_catalog_path(project_root)

    summary: dict[str, object] = {
        "task_file": str(task_path),
        "status": "running",
        "shot_count": len(shots),
        "shots": [],
    }

    for shot in shots:
        character_asset, _ = _resolve_catalog_asset_image(catalog_path, str(shot["character_ref"]), "character")
        scene_asset, _ = _resolve_catalog_asset_image(catalog_path, str(shot["scene_ref"]), "scene")
        effective_prompt = str(shot["video_prompt"] or "").strip() or _build_manju_video_prompt(
            character_name=character_asset.display_name,
            scene_name=scene_asset.display_name,
            storyboard_text=str(shot["storyboard_text"]),
        )
        effective_duration = int(shot["duration_seconds"] or 0) or _estimate_manju_duration_seconds(
            str(shot["storyboard_text"])
        )
        output_path = Path(shot["video_output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)

        process = _run_manju_one_shot_script(
            project_root=project_root,
            image_path=Path(shot["anchor_image_path"]),
            prompt=effective_prompt,
            output_path=output_path,
            mode=str(shot["manju_mode"]),
            resolution=str(shot["manju_resolution"]),
            duration_seconds=effective_duration,
            aspect_ratio=str(shot["aspect_ratio"]),
            model_name=str(shot["manju_model_name"]),
            project_url=str(shot["manju_project_url"]),
            profile_dir=Path(str(shot["manju_profile_dir"])) if str(shot["manju_profile_dir"]) else None,
            headless=bool(shot["manju_headless"]),
        )

        shot_result: dict[str, object] = {
            "storyboard_id": shot["storyboard_id"],
            "character_ref": character_asset.asset_id,
            "scene_ref": scene_asset.asset_id,
            "anchor_image_path": str(shot["anchor_image_path"]),
            "video_output_path": str(output_path),
            "skip_video_audit": bool(shot["skip_video_audit"]),
            "script_exit_code": process.returncode,
            "stdout_tail": process.stdout.strip().splitlines()[-10:],
            "stderr_tail": process.stderr.strip().splitlines()[-10:],
        }

        if process.returncode != 0:
            shot_result["status"] = "failed"
            summary["shots"].append(shot_result)
            summary["status"] = "failed"
            Path(shot["report_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(shot["report_path"]).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1

        is_valid, ffmpeg_error = _verify_video(output_path)
        shot_result["status"] = "success" if is_valid else "decode_failed"
        shot_result["ffmpeg_error"] = ffmpeg_error
        summary["shots"].append(shot_result)
        if not is_valid:
            summary["status"] = "failed"
            Path(shot["report_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(shot["report_path"]).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1

    summary["status"] = "success"
    final_report = _resolve_path(project_root, f"outputs/reviews/{task_path.stem}_batch_report.json")
    Path(final_report).parent.mkdir(parents=True, exist_ok=True)
    Path(final_report).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
