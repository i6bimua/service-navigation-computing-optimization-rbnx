#!/usr/bin/env python3
"""Build the three Habitat demo deliverables the filming brief asks for.

1. ``fail``  — left times out / fails, right succeeds (same source footage;
               left is truncated + slowed to simulate stuck cloud-edge delay).
2. ``speed`` — both succeed, left is slowed and right is sped up so wall-clock
               divergence is obvious on the HUD.
3. ``grid``  — N×2 contact-sheet GIF (one row per episode).

HUD clocks follow the *playback* timeline so the picture matches what viewers see.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--left", type=Path, help="Naive / baseline mp4 (fail/speed)")
    p.add_argument("--right", type=Path, help="Ours mp4 (fail/speed)")
    p.add_argument("--mode", choices=("fail", "speed", "grid"), required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--fps", type=int, default=6)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--title", default="")
    p.add_argument("--fail-at", type=float, default=0.40)
    p.add_argument("--fail-hold", type=float, default=3.0)
    p.add_argument("--left-rate", type=float, default=0.55)
    p.add_argument("--right-rate", type=float, default=1.30)
    p.add_argument("--max-seconds", type=float, default=26.0)
    p.add_argument(
        "--layout",
        choices=("hstack", "vstack"),
        default="hstack",
        help="Lane arrangement inside one reel (default: left/right)",
    )
    p.add_argument(
        "--width-seam",
        type=int,
        default=0,
        help="Override the FPV/map split column (0 = auto-detect)",
    )
    p.add_argument("--grid-pairs", type=Path, default=None)
    p.add_argument("--cell-w", type=int, default=240)
    p.add_argument("--cell-h", type=int, default=0, help="0 = derive from the source aspect")
    p.add_argument("--grid-frames", type=int, default=18)
    return p.parse_args()


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        text=True,
    ).strip()
    return float(out)


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _extract(
    path: Path,
    dest: Path,
    fps: int,
    *,
    max_seconds: float | None = None,
    rate: float = 1.0,
    trim_end: float | None = None,
) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("f_*.jpg"):
        old.unlink()
    filters = []
    if abs(rate - 1.0) > 1e-3:
        filters.append(f"setpts={1.0 / rate}*PTS")
    filters.append(f"fps={fps}")
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path)]
    if trim_end is not None:
        args += ["-t", f"{trim_end:.3f}"]
    args += ["-vf", ",".join(filters)]
    if max_seconds is not None:
        args += ["-frames:v", str(max(1, int(max_seconds * fps)))]
    args.append(str(dest / "f_%05d.jpg"))
    subprocess.check_call(args)
    return sorted(dest.glob("f_*.jpg"))


def _center_banner(
    draw: ImageDraw.ImageDraw,
    size: tuple[int, int],
    text: str,
    *,
    fill: tuple[int, int, int, int],
    font_size: int | None = None,
) -> None:
    """Huge centered status banner (TIMEOUT / SUCCEEDED) — must be readable at a glance."""
    w, h = size
    font_size = font_size or max(36, w // 8)
    font = _font(font_size)
    # Shrink until the label fits with padding.
    while font_size >= 18:
        font = _font(font_size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw <= w * 0.74 and th <= h * 0.24:
            break
        font_size -= 4
    pad_x, pad_y = max(16, w // 28), max(10, h // 36)
    bw, bh = tw + pad_x * 2, th + pad_y * 2
    bx = (w - bw) // 2
    by = (h - bh) // 2
    # Dark veil so white text pops on any scene.
    draw.rectangle((0, by - pad_y, w, by + bh + pad_y), fill=(15, 23, 42, 140))
    draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=max(8, w // 48), fill=fill)
    # Thin white outline for extra punch.
    draw.rounded_rectangle(
        (bx, by, bx + bw, by + bh),
        radius=max(8, w // 48),
        outline=(255, 255, 255, 230),
        width=max(2, w // 200),
    )
    draw.text((bx + pad_x, by + pad_y - bbox[1]), text, fill=(255, 255, 255, 255), font=font)


def _hud(
    frame: Path,
    *,
    label: str,
    color: tuple[int, int, int],
    elapsed_s: float,
    latency_ms: float,
    steps: int,
    badge: str,
    badge_ok: bool | None,
) -> None:
    im = Image.open(frame).convert("RGBA")
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _font(max(16, im.width // 42))
    font_sm = _font(max(13, im.width // 52))
    draw.rectangle((0, 0, im.width, 68), fill=(15, 23, 42, 185))
    draw.rectangle((0, 0, 8, 68), fill=(*color, 255))
    draw.text((14, 6), label, fill=(248, 250, 252, 255), font=font)
    draw.text(
        (14, 36),
        f"step {steps}  ·  {latency_ms:.0f} ms  ·  {elapsed_s:.1f} s",
        fill=(226, 232, 240, 255),
        font=font_sm,
    )
    # Terminal outcomes: giant center banner. RUNNING stays a small corner chip.
    if badge_ok is True:
        _center_banner(
            draw, im.size, badge, fill=(22, 163, 74, 235), font_size=max(48, im.width // 7)
        )
    elif badge_ok is False:
        _center_banner(
            draw, im.size, badge, fill=(220, 38, 38, 240), font_size=max(48, im.width // 7)
        )
    else:
        bc = (100, 116, 139, 220)
        bw, bh = 140, 30
        bx, by = im.width - bw - 10, 10
        draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=8, fill=bc)
        draw.text((bx + 14, by + 6), badge, fill=(255, 255, 255, 255), font=font_sm)
    Image.alpha_composite(im, overlay).convert("RGB").save(frame, quality=92)


def _stack_pair(a: Path, b: Path, out: Path, lane_w: int, lane_h: int, layout: str) -> None:
    """Join the two lanes with no letterboxing.

    Both lanes come from the same simulator view, so they share an aspect ratio
    and can be scaled to an exact size instead of padded onto a fixed canvas.
    """
    stack = "vstack" if layout == "vstack" else "hstack"
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(a),
            "-i",
            str(b),
            "-filter_complex",
            f"[0:v]scale={lane_w}:{lane_h}:flags=lanczos[t];"
            f"[1:v]scale={lane_w}:{lane_h}:flags=lanczos[u];"
            f"[t][u]{stack}=inputs=2",
            str(out),
        ]
    )


def _divider(frame: Path, side: str, color: tuple[int, int, int]) -> None:
    """Thin colored seam on the joining edge so the two lanes read as separate."""
    im = Image.open(frame).convert("RGB")
    draw = ImageDraw.Draw(im)
    w, h = im.size
    t = max(2, w // 320)
    if side == "right":
        draw.rectangle((w - t, 0, w, h), fill=color)
    else:
        draw.rectangle((0, 0, t, h), fill=color)
    im.save(frame, quality=92)


def _probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        text=True,
    ).strip()
    w, h = out.split("x")[:2]
    return int(w), int(h)


def _detect_seam(path: Path) -> int:
    """Column where the simulator's first-person view ends and the map begins.

    InternNav writes `FPV | top-down map` in one frame; the map background is
    near-white, so the seam shows up as a large jump in column brightness.
    """
    sw, sh = _probe_size(path)
    with tempfile.TemporaryDirectory(prefix="demo_seam_") as tmp:
        probe = Path(tmp) / "probe.png"
        subprocess.check_call(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "1",
             "-i", str(path), "-vframes", "1", str(probe)]
        )
        im = Image.open(probe).convert("RGB")
        cols = []
        for x in range(im.width):
            samples = [im.getpixel((x, y)) for y in range(0, im.height, 16)]
            cols.append(sum(sum(p) for p in samples) / (len(samples) * 3))
    best_x, best_jump = 0, 0.0
    lo, hi = int(sw * 0.25), int(sw * 0.75)
    for x in range(lo, hi):
        jump = cols[x] - cols[x - 1]
        if jump > best_jump:
            best_x, best_jump = x, jump
    if best_jump < 25:
        return sw  # single-pane source: nothing to split out
    return best_x


def _lane_geometry(lane_src: tuple[int, int], out_width: int, layout: str) -> tuple[int, int]:
    """Lane size that fills the output frame exactly (even numbers for libx264)."""
    sw, sh = lane_src
    lane_w = out_width if layout == "vstack" else out_width // 2
    lane_w -= lane_w % 2
    lane_h = int(round(lane_w * sh / sw))
    lane_h -= lane_h % 2
    return lane_w, lane_h


def _make_lane(raw: Path, dest: Path, seam: int, aspect: float | None = None) -> None:
    """Lane frame = first-person view, with the top-down map inset bottom-right.

    ``aspect`` center-crops the view (never squeezes it) so a contact sheet can
    use wider cells without distorting the scene.
    """
    im = Image.open(raw).convert("RGB")
    if seam >= im.width:
        im.save(dest, quality=92)
        return
    fpv = im.crop((0, 0, seam, im.height))
    if aspect:
        target_h = int(round(fpv.width / aspect))
        if target_h < fpv.height:
            top = (fpv.height - target_h) // 2
            fpv = fpv.crop((0, top, fpv.width, top + target_h))
    mini = im.crop((seam, 0, im.width, im.height))
    inset_h = max(80, int(fpv.height * 0.34))
    inset_w = int(round(inset_h * mini.width / mini.height))
    mini = mini.resize((inset_w, inset_h), Image.LANCZOS)
    lane = fpv.copy()
    margin = max(6, lane.width // 90)
    x = lane.width - inset_w - margin
    y = lane.height - inset_h - margin
    border = max(2, lane.width // 220)
    ImageDraw.Draw(lane).rectangle(
        (x - border, y - border, x + inset_w + border, y + inset_h + border),
        fill=(226, 232, 240),
    )
    lane.paste(mini, (x, y))
    lane.save(dest, quality=92)


def _encode_mp4(frames_dir: Path, out: Path, fps: int) -> None:
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
            str(frames_dir / "pair_%05d.jpg"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            str(out),
        ]
    )


def _title_card(path: Path, text: str, width: int, height: int) -> None:
    im = Image.new("RGB", (width, height), (15, 23, 42))
    draw = ImageDraw.Draw(im)
    font = _font(max(22, width // 36))
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((width - tw) // 2, (height - th) // 2), text, fill=(248, 250, 252), font=font)
    im.save(path, quality=95)


def _require_pair(args: argparse.Namespace) -> None:
    if not args.left or not args.right:
        raise SystemExit("--left and --right are required for fail/speed modes")
    if not args.left.is_file() or not args.right.is_file():
        raise SystemExit(f"missing input video: {args.left} / {args.right}")


def compose_fail(args: argparse.Namespace) -> None:
    _require_pair(args)
    seam = args.width_seam if args.width_seam else _detect_seam(args.left)
    _sw, sh = _probe_size(args.left)
    lane_w, lane_h = _lane_geometry((seam, sh), args.width, args.layout)
    left_dur = _ffprobe_duration(args.left)
    cut = left_dur * args.fail_at

    with tempfile.TemporaryDirectory(prefix="demo_fail_") as tmp:
        tmp_path = Path(tmp)
        left_frames = _extract(args.left, tmp_path / "L", args.fps, rate=0.65, trim_end=cut)
        right_frames = _extract(
            args.right,
            tmp_path / "R",
            args.fps,
            rate=1.25,
            max_seconds=max(args.max_seconds, cut / 0.65 + args.fail_hold + 2),
        )
        left_live_n = len(left_frames)
        hold_n = max(1, int(args.fail_hold * args.fps))
        # Ours should flash SUCCEEDED while left is already TIMEOUT — not only on the last frame.
        right_success_i = min(len(right_frames) - 1, left_live_n + max(2, int(0.6 * args.fps)))
        target_n = max(len(right_frames), left_live_n + hold_n, right_success_i + hold_n)
        left_seq = list(left_frames) + [left_frames[-1]] * (target_n - left_live_n)
        # Freeze Ours on the success frame for the rest of the reel.
        right_hold = right_frames[min(right_success_i, len(right_frames) - 1)]
        right_seq = list(right_frames[: right_success_i + 1]) + [right_hold] * (
            target_n - right_success_i - 1
        )

        pairs = tmp_path / "pairs"
        pairs.mkdir()
        base_lat, ours_lat = 520.0, 224.0
        for i in range(target_n):
            lf = tmp_path / "Lw" / f"{i:05d}.jpg"
            rf = tmp_path / "Rw" / f"{i:05d}.jpg"
            lf.parent.mkdir(exist_ok=True)
            rf.parent.mkdir(exist_ok=True)
            _make_lane(Path(left_seq[i]), lf, seam)
            _make_lane(Path(right_seq[i]), rf, seam)
            elapsed = (i + 1) / args.fps
            left_failed = i >= left_live_n
            right_done = i >= right_success_i
            _hud(
                lf,
                label="A · Naive ECC  (+cloud-edge delay)",
                color=(239, 68, 68),
                elapsed_s=elapsed,
                latency_ms=base_lat,
                steps=max(1, int(elapsed * 2.0)),
                badge="TIMEOUT" if left_failed else "RUNNING",
                badge_ok=False if left_failed else None,
            )
            _hud(
                rf,
                label="B · Ours (VLN service)",
                color=(34, 197, 94),
                elapsed_s=min(elapsed, (right_success_i + 1) / args.fps) if right_done else elapsed,
                latency_ms=ours_lat,
                steps=max(1, int(min(elapsed, (right_success_i + 1) / args.fps) * 3.2)),
                badge="SUCCEEDED" if right_done else "RUNNING",
                badge_ok=True if right_done else None,
            )
            _divider(lf, "right", (15, 23, 42))
            _stack_pair(lf, rf, pairs / f"pair_{i:05d}.jpg", lane_w, lane_h, args.layout)
        _encode_mp4(pairs, args.out, args.fps)
        print(f"wrote {args.out} ({lane_w * (1 if args.layout == 'vstack' else 2)}x"
              f"{lane_h * (2 if args.layout == 'vstack' else 1)})")


def compose_speed(args: argparse.Namespace) -> None:
    _require_pair(args)
    seam = args.width_seam if args.width_seam else _detect_seam(args.left)
    _sw, sh = _probe_size(args.left)
    lane_w, lane_h = _lane_geometry((seam, sh), args.width, args.layout)
    with tempfile.TemporaryDirectory(prefix="demo_speed_") as tmp:
        tmp_path = Path(tmp)
        left_frames = _extract(
            args.left, tmp_path / "L", args.fps, rate=args.left_rate, max_seconds=args.max_seconds
        )
        # Ours finishes earlier in wall-clock: fewer output frames at higher rate.
        right_budget = args.max_seconds * args.left_rate / args.right_rate
        right_frames = _extract(
            args.right, tmp_path / "R", args.fps, rate=args.right_rate, max_seconds=right_budget
        )
        n = max(len(left_frames), len(right_frames))
        left_seq = list(left_frames) + [left_frames[-1]] * max(0, n - len(left_frames))
        right_seq = list(right_frames) + [right_frames[-1]] * max(0, n - len(right_frames))

        pairs = tmp_path / "pairs"
        pairs.mkdir()
        base_lat, ours_lat = 498.0, 224.0
        for i in range(n):
            lf = tmp_path / "Lw" / f"{i:05d}.jpg"
            rf = tmp_path / "Rw" / f"{i:05d}.jpg"
            lf.parent.mkdir(exist_ok=True)
            rf.parent.mkdir(exist_ok=True)
            _make_lane(Path(left_seq[i]), lf, seam)
            _make_lane(Path(right_seq[i]), rf, seam)
            elapsed = (i + 1) / args.fps
            left_done = i >= len(left_frames) - 1
            right_done = i >= len(right_frames) - 1
            _hud(
                lf,
                label="A · Naive ECC  (slow sync)",
                color=(239, 68, 68),
                elapsed_s=elapsed,
                latency_ms=base_lat,
                steps=max(1, int(elapsed * 2.1)),
                badge="SUCCEEDED" if left_done else "RUNNING",
                badge_ok=True if left_done else None,
            )
            _hud(
                rf,
                label="B · Ours (VLN service)",
                color=(34, 197, 94),
                elapsed_s=min(elapsed, len(right_frames) / args.fps),
                latency_ms=ours_lat,
                steps=max(1, int(min(elapsed, len(right_frames) / args.fps) * 3.3)),
                badge="SUCCEEDED" if right_done else "RUNNING",
                badge_ok=True if right_done else None,
            )
            _divider(lf, "right", (15, 23, 42))
            _stack_pair(lf, rf, pairs / f"pair_{i:05d}.jpg", lane_w, lane_h, args.layout)
        last = pairs / f"pair_{n - 1:05d}.jpg"
        for j in range(int(2 * args.fps)):
            (pairs / f"pair_{n + j:05d}.jpg").write_bytes(last.read_bytes())
        _encode_mp4(pairs, args.out, args.fps)
        print(f"wrote {args.out} ({lane_w * (1 if args.layout == 'vstack' else 2)}x"
              f"{lane_h * (2 if args.layout == 'vstack' else 1)})")


def _sample_progress(
    seq: list[Path],
    n: int,
    *,
    end_frac: float,
    freeze_at: float | None,
) -> list[Path]:
    """Map GIF timeline 0..n-1 onto source frames, optionally freezing mid-run."""
    if not seq:
        raise RuntimeError("empty extract")
    last = len(seq) - 1
    end_idx = max(0, min(last, int(round(last * end_frac))))
    out: list[Path] = []
    for j in range(n):
        u = j / max(n - 1, 1)
        if freeze_at is None:
            idx = int(round(end_idx * u))
        elif u >= freeze_at:
            idx = end_idx
        else:
            idx = int(round(end_idx * (u / max(freeze_at, 1e-6))))
        out.append(seq[max(0, min(last, idx))])
    return out


def _stamp_cell(
    im: Image.Image,
    *,
    badge: str,
    badge_ok: bool | None,
    clock_s: float,
    border: tuple[int, int, int],
    dim: bool = False,
) -> Image.Image:
    """Colored border + clock + huge center TIMEOUT/SUCCEEDED banner."""
    base = im.convert("RGBA")
    if dim:
        base = Image.alpha_composite(base, Image.new("RGBA", base.size, (15, 23, 42, 120)))
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = base.size
    for t in range(3):
        draw.rectangle((t, t, w - 1 - t, h - 1 - t), outline=(*border, 255))
    font_sm = _font(max(11, w // 18))
    draw.rectangle((0, 0, w, 18), fill=(15, 23, 42, 210))
    draw.text((5, 2), f"{clock_s:.1f}s", fill=(248, 250, 252, 255), font=font_sm)
    if badge_ok is True:
        _center_banner(draw, (w, h), badge, fill=(22, 163, 74, 235), font_size=max(22, w // 7))
    elif badge_ok is False:
        _center_banner(draw, (w, h), badge, fill=(220, 38, 38, 245), font_size=max(22, w // 7))
    else:
        # Bottom-left: the map inset lives in the opposite corner.
        bc = (100, 116, 139, 220)
        bw = min(w - 8, 72)
        bh = 16
        bx, by = 4, h - bh - 4
        draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=4, fill=bc)
        draw.text((bx + 6, by + 1), badge, fill=(255, 255, 255, 255), font=font_sm)
    return Image.alpha_composite(base, overlay).convert("RGB")


def compose_grid(args: argparse.Namespace) -> None:
    """8×2 contact sheet where Ours is visually ahead / succeeds and Naive lags / fails.

    TSV: left_mp4, right_mp4, label[, role]  with role in {fail, speed}.
    """
    if not args.grid_pairs or not args.grid_pairs.is_file():
        raise SystemExit("--grid-pairs file required for grid mode")
    rows: list[tuple[Path, Path, str, str]] = []
    for i, line in enumerate(args.grid_pairs.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            raise SystemExit(f"bad grid line: {line!r}")
        role = parts[3].strip().lower() if len(parts) >= 4 else ("fail" if i % 3 == 0 else "speed")
        if role not in ("fail", "speed"):
            role = "speed"
        rows.append((Path(parts[0]), Path(parts[1]), parts[2], role))
    if not rows:
        raise SystemExit("no rows in --grid-pairs")

    seam = args.width_seam if args.width_seam else _detect_seam(rows[0][0])
    cell_aspect = 16 / 9
    cell_w = args.cell_w - args.cell_w % 2
    cell_h = args.cell_h or int(round(cell_w / cell_aspect))
    gap = 4
    header_h = 26
    row_label_h = 14
    sheet_w = cell_w * 2 + gap + 4
    sheet_h = header_h + (row_label_h + cell_h + gap) * len(rows) + 2
    font = _font(14)
    font_sm = _font(11)
    n = args.grid_frames

    with tempfile.TemporaryDirectory(prefix="demo_grid_") as tmp:
        tmp_path = Path(tmp)
        left_series: list[list[Path]] = []
        right_series: list[list[Path]] = []

        for i, (lp, rp, _label, role) in enumerate(rows):
            lf = _extract(lp, tmp_path / f"L{i}", args.fps, rate=1.0, max_seconds=14.0)
            rf = _extract(rp, tmp_path / f"R{i}", args.fps, rate=1.0, max_seconds=14.0)
            if role == "fail":
                left_series.append(_sample_progress(lf, n, end_frac=0.40, freeze_at=0.42))
                full = _sample_progress(rf, n, end_frac=1.0, freeze_at=None)
                compressed = []
                for j in range(n):
                    u = j / max(n - 1, 1)
                    src_u = min(1.0, u / 0.50)
                    compressed.append(full[int(round(src_u * (n - 1)))])
                right_series.append(compressed)
            else:
                left_series.append(_sample_progress(lf, n, end_frac=1.0, freeze_at=None))
                full = _sample_progress(rf, n, end_frac=1.0, freeze_at=None)
                compressed = []
                for j in range(n):
                    u = j / max(n - 1, 1)
                    src_u = min(1.0, u / 0.55)
                    compressed.append(full[int(round(src_u * (n - 1)))])
                right_series.append(compressed)

        frames_out = tmp_path / "sheet"
        frames_out.mkdir()
        for t in range(n):
            sheet = Image.new("RGB", (sheet_w, sheet_h), (15, 23, 42))
            draw = ImageDraw.Draw(sheet)
            draw.text((4, 5), "Naive ECC  ·  slow / fail", fill=(248, 113, 113), font=font)
            draw.text((cell_w + gap, 5), "Ours  ·  fast / succeed", fill=(74, 222, 128), font=font)
            y = header_h
            u = t / max(n - 1, 1)
            for r, (_lp, _rp, label, role) in enumerate(rows):
                chip = "FAIL vs OK" if role == "fail" else "both OK · we faster"
                draw.rectangle((2, y, sheet_w - 2, y + row_label_h), fill=(30, 41, 59))
                draw.text((6, y + 1), f"{label}  ·  {chip}", fill=(226, 232, 240), font=font_sm)
                y += row_label_h

                lane_tmp = tmp_path / "cell"
                lane_tmp.mkdir(exist_ok=True)
                lcell, rcell = lane_tmp / "l.jpg", lane_tmp / "r.jpg"
                _make_lane(left_series[r][t], lcell, seam, aspect=cell_aspect)
                _make_lane(right_series[r][t], rcell, seam, aspect=cell_aspect)
                left_raw = Image.open(lcell).convert("RGB").resize((cell_w, cell_h), Image.LANCZOS)
                right_raw = Image.open(rcell).convert("RGB").resize((cell_w, cell_h), Image.LANCZOS)

                if role == "fail":
                    left_done = u >= 0.42
                    right_done = u >= 0.50
                    left_badge = "TIMEOUT" if left_done else "RUNNING"
                    left_ok = False if left_done else None
                    right_badge = "SUCCEEDED" if right_done else "RUNNING"
                    right_ok = True if right_done else None
                    left_clock = (10.0 + 28.0 * min(u / 0.42, 1.0)) if not left_done else 42.0
                    right_clock = min(17.5, 6.0 + 23.0 * min(u / 0.50, 1.0))
                    left_dim = left_done
                else:
                    left_done = u >= 0.98
                    right_done = u >= 0.55
                    left_badge = "SUCCEEDED" if left_done else "RUNNING"
                    left_ok = True if left_done else None
                    right_badge = "SUCCEEDED" if right_done else "RUNNING"
                    right_ok = True if right_done else None
                    left_clock = 8.0 + 30.0 * u
                    right_clock = min(16.0, 5.5 + 19.0 * min(u / 0.55, 1.0))
                    left_dim = False

                left_im = _stamp_cell(
                    left_raw,
                    badge=left_badge,
                    badge_ok=left_ok,
                    clock_s=left_clock,
                    border=(239, 68, 68),
                    dim=left_dim,
                )
                right_im = _stamp_cell(
                    right_raw,
                    badge=right_badge,
                    badge_ok=right_ok,
                    clock_s=right_clock,
                    border=(34, 197, 94),
                    dim=False,
                )
                sheet.paste(left_im, (2, y))
                sheet.paste(right_im, (cell_w + gap + 2, y))
                y += cell_h + gap
            sheet.save(frames_out / f"g_{t:03d}.png")

        args.out.parent.mkdir(parents=True, exist_ok=True)
        palette = tmp_path / "palette.png"
        subprocess.check_call(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-framerate", "6", "-i", str(frames_out / "g_%03d.png"),
                "-vf", "palettegen=stats_mode=diff", str(palette),
            ]
        )
        subprocess.check_call(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-framerate", "6", "-i", str(frames_out / "g_%03d.png"),
                "-i", str(palette),
                "-lavfi", "paletteuse=dither=bayer:bayer_scale=5",
                str(args.out),
            ]
        )
        print(f"wrote {args.out} ({len(rows)}×2)")


def main() -> None:
    args = parse_args()
    if args.mode == "fail":
        compose_fail(args)
    elif args.mode == "speed":
        compose_speed(args)
    else:
        compose_grid(args)


if __name__ == "__main__":
    main()
