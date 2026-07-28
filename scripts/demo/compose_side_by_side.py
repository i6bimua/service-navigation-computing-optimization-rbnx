#!/usr/bin/env python3
"""Compose a left/right Habitat comparison reel with on-screen HUD badges.

Looks under ``--run-dir/{naive_ecc,ours}/`` for InternNav ``vis_*/**/*.mp4``
(or ``*.mp4`` anywhere in the lane) and for episode summaries / result.json
to fill success and latency fields.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


LANE_DEFAULTS = {
    "naive_ecc": {"label": "A · Naive ECC", "badge_fail": "FAIL", "color": (239, 68, 68)},
    "ours": {"label": "B · Ours (VLN service)", "badge_ok": "SUCCEEDED", "color": (34, 197, 94)},
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--left", default="naive_ecc", help="Left lane directory name")
    p.add_argument("--right", default="ours", help="Right lane directory name")
    p.add_argument("--episode", default="", help="Prefer this episode id when several mp4s exist")
    p.add_argument("--fps", type=int, default=6)
    p.add_argument("--width", type=int, default=1280, help="Output width (height follows 16:9)")
    p.add_argument("--max-seconds", type=float, default=45.0, help="Cap each lane before the end freeze")
    p.add_argument("--freeze-seconds", type=float, default=2.0)
    return p.parse_args()


def _find_videos(lane_dir: Path) -> list[Path]:
    if not lane_dir.is_dir():
        return []
    vids = sorted(lane_dir.rglob("*.mp4"))
    # Prefer InternNav vis_* trees.
    preferred = [v for v in vids if "/vis_" in str(v) or "\\vis_" in str(v)]
    return preferred or vids


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _episode_metrics(lane_dir: Path, episode: str) -> dict[str, Any]:
    """Best-effort metrics from result.json / analysis logs / summary CSV."""
    out: dict[str, Any] = {
        "success": None,
        "steps": None,
        "mean_step_latency_ms": None,
        "elapsed_s": None,
        "detail": "",
    }
    progress = _load_json(lane_dir / "eval" / "progress.json")
    if isinstance(progress, dict):
        if progress.get("success") is not None:
            out["success"] = float(progress["success"]) >= 0.5
        out["steps"] = progress.get("steps") or out["steps"]
        if progress.get("wall_time") is not None:
            out["elapsed_s"] = float(progress["wall_time"])

    result = lane_dir / "eval" / "result.json"
    payload = _load_json(result)
    if isinstance(payload, dict):
        for key in ("success", "sr", "Success", "sucs_all"):
            if key in payload and out["success"] is None:
                out["success"] = float(payload[key]) >= 0.5
                break
        if "episodes" in payload and isinstance(payload["episodes"], list):
            for ep in payload["episodes"]:
                if str(ep.get("episode_id", ep.get("id", ""))) == str(episode) or not episode:
                    out["success"] = float(ep.get("success", 0)) >= 0.5
                    out["steps"] = ep.get("steps") or ep.get("steps_taken")
                    break

    for log in sorted((lane_dir / "eval").rglob("*.json")) if (lane_dir / "eval").is_dir() else []:
        if episode and episode not in log.name and f"{int(episode):04d}" not in log.name:
            continue
        data = _load_json(log)
        if not isinstance(data, dict):
            continue
        if "success" in data:
            out["success"] = float(data["success"]) >= 0.5
        if "steps" in data:
            out["steps"] = data["steps"]
        step_logs = data.get("step_logs") or data.get("control_steps")
        if isinstance(step_logs, list) and step_logs:
            latencies = []
            for row in step_logs:
                if not isinstance(row, dict):
                    continue
                for k in ("step_latency_ms", "s1_latency_ms", "total_latency_ms", "latency_ms"):
                    if k in row and row[k] is not None:
                        try:
                            latencies.append(float(row[k]))
                        except (TypeError, ValueError):
                            pass
                        break
            if latencies:
                out["mean_step_latency_ms"] = sum(latencies) / len(latencies)
                out["steps"] = out["steps"] or len(latencies)

    summary = _load_json(lane_dir / "edge_cloud_s2_summary.json")
    if isinstance(summary, dict) and out["mean_step_latency_ms"] is None:
        for k in ("mean_s1_round_trip_wall_time_ms", "mean_sync_total_latency_ms"):
            if summary.get(k) is not None:
                out["mean_step_latency_ms"] = float(summary[k])
                break

    meta = _load_json(lane_dir / "lane_meta.json") or {}
    out["label"] = meta.get("label") or LANE_DEFAULTS.get(lane_dir.name, {}).get("label", lane_dir.name)
    return out


def _pick_video(lane_dir: Path, episode: str) -> Path:
    vids = _find_videos(lane_dir)
    if not vids:
        raise FileNotFoundError(
            f"No .mp4 under {lane_dir}. Re-run with ANALYSIS_SAVE_VIDEO=1 "
            f"(scripts/demo/run_comparison.sh sets this)."
        )
    if episode:
        for v in vids:
            if episode in v.name or f"{int(episode):04d}" in v.name:
                return v
    return vids[0]


def _probe_duration(path: Path) -> float:
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        text=True,
    ).strip()
    return float(raw)


def _draw_hud_frame(
    frame_path: Path,
    *,
    label: str,
    latency_ms: float | None,
    steps: int | None,
    elapsed_s: float | None,
    success: bool | None,
    color: tuple[int, int, int],
) -> None:
    from PIL import Image, ImageDraw, ImageFont

    im = Image.open(frame_path).convert("RGBA")
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(18, im.width // 40))
        font_sm = ImageFont.truetype("DejaVuSans.ttf", max(14, im.width // 50))
    except OSError:
        font = ImageFont.load_default()
        font_sm = font

    badge = "SUCCEEDED" if success else ("FAIL" if success is False else "RUNNING")
    badge_color = (34, 197, 94, 220) if success else ((239, 68, 68, 220) if success is False else (148, 163, 184, 220))
    lat = f"{latency_ms:.0f} ms" if latency_ms is not None else "— ms"
    step_s = f"step {steps}" if steps is not None else "step —"
    elap = f"{elapsed_s:.1f} s" if elapsed_s is not None else "— s"
    line1 = f"{label}"
    line2 = f"{step_s}  ·  {lat}  ·  {elap}"

    pad = 12
    box_h = 72
    draw.rectangle((0, 0, im.width, box_h), fill=(15, 23, 42, 180))
    draw.rectangle((0, 0, 8, box_h), fill=(*color, 255))
    draw.text((pad + 8, 8), line1, fill=(248, 250, 252, 255), font=font)
    draw.text((pad + 8, 38), line2, fill=(226, 232, 240, 255), font=font_sm)

    bw, bh = 160, 36
    bx, by = im.width - bw - pad, pad
    draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=8, fill=badge_color)
    draw.text((bx + 16, by + 8), badge, fill=(255, 255, 255, 255), font=font_sm)

    Image.alpha_composite(im, overlay).convert("RGB").save(frame_path, quality=92)


def _extract_frames(video: Path, work: Path, fps: int, max_seconds: float) -> list[Path]:
    work.mkdir(parents=True, exist_ok=True)
    pattern = work / "frame_%05d.jpg"
    duration = min(_probe_duration(video), max_seconds)
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-t",
            f"{duration:.3f}",
            "-vf",
            f"fps={fps}",
            str(pattern),
        ]
    )
    return sorted(work.glob("frame_*.jpg"))


def _write_side_by_side(
    left_frames: list[Path],
    right_frames: list[Path],
    out: Path,
    *,
    width: int,
    fps: int,
    freeze_seconds: float,
) -> None:
    n = max(len(left_frames), len(right_frames))
    if n == 0:
        raise RuntimeError("no frames to compose")
    height = int(width * 9 / 16)
    half = width // 2
    cell_h = height

    with tempfile.TemporaryDirectory(prefix="demo_compose_") as tmp:
        tmp_path = Path(tmp)
        for i in range(n):
            lf = left_frames[min(i, len(left_frames) - 1)]
            rf = right_frames[min(i, len(right_frames) - 1)]
            target = tmp_path / f"pair_{i:05d}.jpg"
            subprocess.check_call(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(lf),
                    "-i",
                    str(rf),
                    "-filter_complex",
                    f"[0:v]scale={half}:{cell_h}:force_original_aspect_ratio=decrease,"
                    f"pad={half}:{cell_h}:(ow-iw)/2:(oh-ih)/2:black[l];"
                    f"[1:v]scale={half}:{cell_h}:force_original_aspect_ratio=decrease,"
                    f"pad={half}:{cell_h}:(ow-iw)/2:(oh-ih)/2:black[r];"
                    f"[l][r]hstack=inputs=2",
                    str(target),
                ]
            )
        # Freeze last frame.
        freeze_n = max(1, int(math.ceil(freeze_seconds * fps)))
        last = tmp_path / f"pair_{n - 1:05d}.jpg"
        for j in range(freeze_n):
            (tmp_path / f"pair_{n + j:05d}.jpg").write_bytes(last.read_bytes())

        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-framerate",
                str(fps),
                "-i",
                str(tmp_path / "pair_%05d.jpg"),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "18",
                str(out),
            ]
        )


def main() -> None:
    args = parse_args()
    left_dir = args.run_dir / args.left
    right_dir = args.run_dir / args.right
    episode = str(args.episode).strip()

    left_video = _pick_video(left_dir, episode)
    right_video = _pick_video(right_dir, episode)
    # Infer episode from filename if not given.
    if not episode:
        m = re.search(r"(\d{1,4})", left_video.stem)
        episode = m.group(1) if m else ""

    left_m = _episode_metrics(left_dir, episode)
    right_m = _episode_metrics(right_dir, episode)
    left_color = LANE_DEFAULTS.get(args.left, {}).get("color", (148, 163, 184))
    right_color = LANE_DEFAULTS.get(args.right, {}).get("color", (34, 197, 94))

    with tempfile.TemporaryDirectory(prefix="demo_frames_") as tmp:
        tmp_path = Path(tmp)
        left_frames = _extract_frames(left_video, tmp_path / "left", args.fps, args.max_seconds)
        right_frames = _extract_frames(right_video, tmp_path / "right", args.fps, args.max_seconds)

        left_elapsed = _probe_duration(left_video)
        right_elapsed = _probe_duration(right_video)
        for i, frame in enumerate(left_frames):
            terminal = i == len(left_frames) - 1
            _draw_hud_frame(
                frame,
                label=str(left_m["label"]),
                latency_ms=left_m["mean_step_latency_ms"],
                steps=left_m["steps"],
                elapsed_s=min(left_elapsed, args.max_seconds) * (i + 1) / max(len(left_frames), 1),
                success=left_m["success"] if terminal else None,
                color=left_color,
            )
        for i, frame in enumerate(right_frames):
            terminal = i == len(right_frames) - 1
            _draw_hud_frame(
                frame,
                label=str(right_m["label"]),
                latency_ms=right_m["mean_step_latency_ms"],
                steps=right_m["steps"],
                elapsed_s=min(right_elapsed, args.max_seconds) * (i + 1) / max(len(right_frames), 1),
                success=right_m["success"] if terminal else None,
                color=right_color,
            )

        _write_side_by_side(
            left_frames,
            right_frames,
            args.out,
            width=args.width,
            fps=args.fps,
            freeze_seconds=args.freeze_seconds,
        )

    print(
        json.dumps(
            {
                "out": str(args.out),
                "left": str(left_video),
                "right": str(right_video),
                "left_success": left_m["success"],
                "right_success": right_m["success"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
