"""Render repository-owned SVG cards from authenticated GitHub profile data.

The contribution values are GitHub contribution-calendar metrics for a configured
period, not an inferred lifetime commit count. See docs/statistics.md.
"""

from __future__ import annotations

import argparse
import html
import json
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
from typing import Any


THEMES = {
    "light": {
        "background": "#FFFFFF",
        "border": "#D8DEE4",
        "title": "#2563EB",
        "text": "#0F172A",
        "muted": "#64748B",
        "cyan": "#0891B2",
        "violet": "#7C3AED",
        "track": "#E2E8F0",
    },
    "dark": {
        "background": "#0B1220",
        "border": "#24324A",
        "title": "#60A5FA",
        "text": "#E2E8F0",
        "muted": "#94A3B8",
        "cyan": "#22D3EE",
        "violet": "#A78BFA",
        "track": "#1E293B",
    },
}
LANGUAGE_COLORS = ["#60A5FA", "#22D3EE", "#A78BFA", "#34D399", "#F59E0B", "#F472B6"]


def calculate_streaks(days: list[dict[str, Any]], period_end: date) -> tuple[int, int]:
    counts = {date.fromisoformat(item["date"]): int(item.get("count") or 0) for item in days}
    ordered_dates = sorted(counts)
    longest = 0
    run = 0
    previous: date | None = None
    for day in ordered_dates:
        if counts[day] <= 0:
            run = 0
        elif previous is not None and day == previous + timedelta(days=1) and counts.get(previous, 0) > 0:
            run += 1
        else:
            run = 1
        longest = max(longest, run)
        previous = day

    anchor = period_end
    if counts.get(anchor, 0) == 0 and counts.get(anchor - timedelta(days=1), 0) > 0:
        anchor -= timedelta(days=1)
    current = 0
    while counts.get(anchor, 0) > 0:
        current += 1
        anchor -= timedelta(days=1)
    return current, longest


def _base_svg(width: int, height: int, theme: dict[str, str], title: str, description: str, body: str) -> str:
    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc">
  <title id="title">{html.escape(title)}</title>
  <desc id="desc">{html.escape(description)}</desc>
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" fill="{theme['background']}" stroke="{theme['border']}"/>
  <style>text{{font-family:'Segoe UI',Inter,Arial,sans-serif}}.label{{font-size:13px;fill:{theme['muted']}}}.value{{font-size:20px;font-weight:700;fill:{theme['text']}}}.heading{{font-size:17px;font-weight:700;fill:{theme['title']}}}.foot{{font-size:11px;fill:{theme['muted']}}}</style>
{body}
</svg>
"""


def render_activity_card(data: dict[str, Any], variant: str) -> str:
    theme = THEMES[variant]
    stats = data["stats"]
    period = data["reporting_period"]
    rows = [
        ("Public repositories", stats["public_repositories"]),
        ("Eligible commit contributions", stats["public_commit_contributions"]),
        ("Pull-request contributions", stats["public_pull_request_contributions"]),
        ("Stars / forks on eligible public repos", f"{stats['total_stars']} / {stats['total_forks']}"),
    ]
    body = ['  <text class="heading" x="24" y="32">GitHub Activity</text>']
    for index, (label, value) in enumerate(rows):
        y = 64 + index * 28
        body.append(f'  <text class="label" x="24" y="{y}">{html.escape(str(label))}</text>')
        body.append(f'  <text class="value" x="466" y="{y}" text-anchor="end">{html.escape(str(value))}</text>')
    footer = f"Contribution metrics: {period['start']} to {period['end']} · {period['timezone']}"
    body.append(f'  <text class="foot" x="24" y="184">{html.escape(footer)}</text>')
    return _base_svg(
        490,
        200,
        theme,
        "GitHub activity statistics",
        "Public repository totals and eligible GitHub contribution metrics for the selected reporting period.",
        "\n".join(body),
    )


def render_language_card(data: dict[str, Any], variant: str) -> str:
    theme = THEMES[variant]
    languages = data.get("languages", [])[:6]
    body = ['  <text class="heading" x="24" y="32">Public Repository Languages</text>']
    if not languages:
        body.append('  <text class="label" x="24" y="78">No language data available</text>')
    for index, language in enumerate(languages):
        y = 58 + index * 23
        percentage = float(language.get("percentage") or 0)
        width = round(min(max(percentage, 0), 100) * 2.5, 1)
        color = LANGUAGE_COLORS[index % len(LANGUAGE_COLORS)]
        body.extend(
            [
                f'  <text class="label" x="24" y="{y}">{html.escape(str(language["name"]))}</text>',
                f'  <text class="label" x="326" y="{y}" text-anchor="end">{percentage:.1f}%</text>',
                f'  <rect x="24" y="{y + 6}" width="250" height="5" rx="2.5" fill="{theme["track"]}"/>',
                f'  <rect x="24" y="{y + 6}" width="{width}" height="5" rx="2.5" fill="{color}"/>',
            ]
        )
    body.append('  <text class="foot" x="24" y="190">Eligible public repositories · GitHub Linguist bytes</text>')
    return _base_svg(
        350,
        200,
        theme,
        "Public repository language distribution",
        "Language percentages derived from GitHub Linguist byte totals across eligible public repositories.",
        "\n".join(body),
    )


def render_streak_card(data: dict[str, Any], variant: str) -> str:
    theme = THEMES[variant]
    period = data["reporting_period"]
    days = data["contribution_calendar"]
    current, longest = calculate_streaks(days, date.fromisoformat(period["end"]))
    visible_total = sum(int(day.get("count") or 0) for day in days)
    columns = [
        ("Visible Contributions", visible_total, theme["cyan"]),
        ("Current Streak", current, theme["violet"]),
        ("Longest Streak", longest, theme["title"]),
    ]
    body = ['  <text class="heading" x="260" y="32" text-anchor="middle">Contribution Consistency</text>']
    for index, (label, value, color) in enumerate(columns):
        x = 87 + index * 173
        body.append(f'  <text x="{x}" y="96" text-anchor="middle" font-size="34" font-weight="700" fill="{color}">{value}</text>')
        body.append(f'  <text class="label" x="{x}" y="122" text-anchor="middle">{html.escape(label)}</text>')
        if index < 2:
            body.append(f'  <path d="M{x + 86} 58V132" stroke="{theme["border"]}"/>')
    footer = f"GitHub-visible calendar · rolling {period['days']} days · through {period['end']} ({period['timezone']})"
    body.append(f'  <text class="foot" x="260" y="171" text-anchor="middle">{html.escape(footer)}</text>')
    return _base_svg(
        520,
        185,
        theme,
        "Contribution streak statistics",
        "Visible contribution total, current streak, and longest streak within the rolling reporting period.",
        "\n".join(body),
    )


def render_cards(data: dict[str, Any]) -> dict[str, str]:
    days = data.get("contribution_calendar") or []
    stats = data.get("stats") or {}
    if not days:
        raise ValueError("Authenticated contribution calendar data is required to generate statistics cards")
    if stats.get("public_commit_contributions") is None:
        raise ValueError("Eligible commit contribution data is missing")
    calendar_sum = sum(int(day.get("count") or 0) for day in days)
    api_total = stats.get("visible_contributions")
    if api_total is not None and int(api_total) != calendar_sum:
        raise ValueError("Contribution calendar total does not match its daily values")

    cards: dict[str, str] = {}
    for variant, suffix in (("light", ""), ("dark", "-dark")):
        cards[f"github-stats{suffix}.svg"] = render_activity_card(data, variant)
        cards[f"language-stats{suffix}.svg"] = render_language_card(data, variant)
        cards[f"streak-stats{suffix}.svg"] = render_streak_card(data, variant)
    for name, content in cards.items():
        try:
            ET.fromstring(content)
        except ET.ParseError as error:
            raise ValueError(f"Generated invalid SVG {name}: {error}") from error
    return cards


def write_cards_if_changed(output_directory: Path, cards: dict[str, str]) -> list[str]:
    output_directory.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []
    for name, content in cards.items():
        destination = output_directory / name
        if destination.exists() and destination.read_text(encoding="utf-8") == content:
            continue
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(destination)
        changed.append(name)
    return changed


def generate_stats(data_path: Path, output_directory: Path) -> list[str]:
    data = json.loads(data_path.read_text(encoding="utf-8"))
    cards = render_cards(data)
    return write_cards_if_changed(output_directory, cards)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate repository-owned GitHub statistics SVG cards.")
    parser.add_argument("--data", type=Path, default=Path(".profile-data.json"))
    parser.add_argument("--output", type=Path, default=Path("assets/generated"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        changed = generate_stats(args.data, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Statistics generation failed safely: {error}")
        return 1
    print("Updated statistics cards: " + ", ".join(changed) if changed else "Statistics cards are already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
