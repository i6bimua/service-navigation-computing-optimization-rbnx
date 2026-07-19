from __future__ import annotations

import csv
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results"
OUTPUT = ROOT / "docs" / "assets" / "benchmark_overview.svg"


def read_rows() -> list[dict[str, str]]:
    with (RESULTS / "main_results.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def row(rows: list[dict[str, str]], platform: str, method: str) -> dict[str, str]:
    for item in rows:
        if item["platform"] == platform and item["method"] == method:
            return item
    raise KeyError(f"missing benchmark row: {platform} / {method}")


def text(x: float, y: float, value: str, css_class: str, anchor: str = "start") -> str:
    return (
        f'<text x="{x}" y="{y}" class="{css_class}" '
        f'text-anchor="{anchor}">{escape(value)}</text>'
    )


def card(x: int, label: str, value: str, detail: str, accent: str) -> list[str]:
    return [
        f'<rect x="{x}" y="150" width="300" height="168" rx="18" class="card"/>',
        f'<rect x="{x}" y="150" width="8" height="168" rx="4" fill="{accent}"/>',
        text(x + 28, 190, label, "card-label"),
        text(x + 28, 246, value, "card-value"),
        text(x + 28, 285, detail, "card-detail"),
    ]


def comparison_bar(
    x: int,
    y: int,
    width: int,
    label: str,
    value: float,
    maximum: float,
    color: str,
    suffix: str,
) -> list[str]:
    filled = max(2.0, width * value / maximum)
    return [
        text(x, y - 8, label, "bar-label"),
        f'<rect x="{x}" y="{y}" width="{width}" height="20" rx="10" class="bar-bg"/>',
        f'<rect x="{x}" y="{y}" width="{filled:.1f}" height="20" rx="10" fill="{color}"/>',
        text(x + width + 18, y + 16, f"{value:g}{suffix}", "bar-value"),
    ]


def render() -> str:
    rows = read_rows()
    edge = row(rows, "Orin+A100", "Edge Only")
    naive = row(rows, "Orin+A100", "Naive ECC")
    ours = row(rows, "Orin+A100", "Compute Skill")

    edge_latency = float(edge["avg_step_latency_ms"])
    ours_latency = float(ours["avg_step_latency_ms"])
    naive_sr = float(naive["sr_pct"])
    ours_sr = float(ours["sr_pct"])
    naive_spl = float(naive["spl_pct"])
    ours_spl = float(ours["spl_pct"])

    speedup = edge_latency / ours_latency
    sr_gain = ours_sr - naive_sr
    spl_gain = ours_spl - naive_spl

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="700" '
        'viewBox="0 0 1400 700" role="img" '
        'aria-label="RoboNix Compute Optimization Skill R2R-CE benchmark overview">',
        "<defs>",
        "<style>",
        "text { font-family: Inter, Arial, sans-serif; }",
        ".title { font-size: 38px; font-weight: 750; fill: #0f172a; }",
        ".subtitle { font-size: 18px; fill: #475569; }",
        ".card { fill: #ffffff; stroke: #dbe4ee; stroke-width: 2; }",
        ".card-label { font-size: 17px; font-weight: 650; fill: #475569; }",
        ".card-value { font-size: 38px; font-weight: 780; fill: #0f172a; }",
        ".card-detail { font-size: 15px; fill: #64748b; }",
        ".section { font-size: 21px; font-weight: 700; fill: #0f172a; }",
        ".bar-label { font-size: 15px; font-weight: 650; fill: #334155; }",
        ".bar-value { font-size: 15px; font-weight: 700; fill: #334155; }",
        ".bar-bg { fill: #e8eef5; }",
        ".note { font-size: 14px; fill: #64748b; }",
        "</style>",
        "</defs>",
        '<rect width="1400" height="700" fill="#f7fafc"/>',
        text(60, 66, "R2R-CE Performance at a Glance", "title"),
        text(
            60,
            104,
            "DualVLN · val-unseen (1,839 episodes) · NVIDIA Orin + A100",
            "subtitle",
        ),
    ]
    parts.extend(card(60, "Latency speedup", f"{speedup:.2f}×", "vs. Edge Only", "#2563eb"))
    parts.extend(card(380, "Success rate", f"+{sr_gain:.1f} pt", f"{naive_sr:g}% → {ours_sr:g}%", "#16a34a"))
    parts.extend(card(700, "SPL recovery", f"+{spl_gain:.1f} pt", f"{naive_spl:g} → {ours_spl:g}", "#f59e0b"))
    parts.extend(card(1020, "Runtime overhead", "< 8 KB", "buffer + control state", "#7c3aed"))

    parts.extend(
        [
            text(60, 380, "Accuracy recovery over Naive ECC", "section"),
            text(735, 380, "Edge latency reduction", "section"),
        ]
    )
    parts.extend(comparison_bar(60, 430, 430, "Naive ECC SR", naive_sr, 70, "#94a3b8", "%"))
    parts.extend(comparison_bar(60, 486, 430, "Compute Skill SR", ours_sr, 70, "#16a34a", "%"))
    parts.extend(comparison_bar(60, 560, 430, "Naive ECC SPL", naive_spl, 65, "#94a3b8", ""))
    parts.extend(comparison_bar(60, 616, 430, "Compute Skill SPL", ours_spl, 65, "#f59e0b", ""))
    parts.extend(comparison_bar(735, 430, 430, "Edge Only", edge_latency, 520, "#94a3b8", " ms"))
    parts.extend(comparison_bar(735, 486, 430, "Compute Skill", ours_latency, 520, "#2563eb", " ms"))
    parts.extend(
        [
            text(
                735,
                570,
                "Validated project benchmark on the complete val-unseen split.",
                "note",
            ),
            text(
                735,
                600,
                "See benchmarks/r2r_ce for all baselines, Thor results, and metadata.",
                "note",
            ),
            "</svg>",
        ]
    )
    return "\n".join(parts) + "\n"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
