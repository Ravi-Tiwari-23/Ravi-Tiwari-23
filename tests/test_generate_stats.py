from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import date

import pytest

from scripts.generate_stats import calculate_streaks, generate_stats, render_cards, write_cards_if_changed


def sample_data(*, final_count: int = 0) -> dict:
    days = [
        {"date": "2026-01-01", "count": 1},
        {"date": "2026-01-02", "count": 1},
        {"date": "2026-01-03", "count": 0},
        {"date": "2026-01-04", "count": 2},
        {"date": "2026-01-05", "count": final_count},
    ]
    return {
        "stats": {
            "public_repositories": 8,
            "total_stars": 1,
            "total_forks": 3,
            "public_commit_contributions": 12,
            "public_pull_request_contributions": 3,
            "visible_contributions": sum(day["count"] for day in days),
        },
        "reporting_period": {
            "start": "2026-01-01",
            "end": "2026-01-05",
            "days": 5,
            "timezone": "Asia/Kolkata",
        },
        "contribution_calendar": days,
        "languages": [
            {"name": "Python", "percentage": 70.0},
            {"name": "JavaScript", "percentage": 30.0},
        ],
    }


def test_streaks_use_the_selected_period_and_allow_today_to_be_incomplete():
    current, longest = calculate_streaks(sample_data()["contribution_calendar"], date(2026, 1, 5))
    assert current == 1
    assert longest == 2


def test_all_generated_cards_are_valid_svg_and_show_intended_period():
    cards = render_cards(sample_data())
    assert len(cards) == 6
    assert set(cards) == {
        "github-stats.svg",
        "github-stats-dark.svg",
        "language-stats.svg",
        "language-stats-dark.svg",
        "streak-stats.svg",
        "streak-stats-dark.svg",
    }
    for content in cards.values():
        assert ET.fromstring(content).tag.endswith("svg")
    assert "2026-01-01 to 2026-01-05" in cards["github-stats.svg"]
    assert "rolling 5 days" in cards["streak-stats.svg"]


def test_changed_api_data_changes_generated_statistics():
    before = render_cards(sample_data(final_count=0))
    after = render_cards(sample_data(final_count=3))
    assert before["streak-stats.svg"] != after["streak-stats.svg"]
    assert ">4<" in before["streak-stats.svg"]
    assert ">7<" in after["streak-stats.svg"]


def test_identical_data_does_not_rewrite_cards(tmp_path):
    cards = render_cards(sample_data())
    assert len(write_cards_if_changed(tmp_path, cards)) == 6
    assert write_cards_if_changed(tmp_path, cards) == []


def test_missing_api_data_preserves_previously_valid_cards(tmp_path):
    existing = tmp_path / "github-stats.svg"
    existing.write_text("<svg/>", encoding="utf-8")
    data_path = tmp_path / "data.json"
    invalid = sample_data()
    invalid["contribution_calendar"] = []
    data_path.write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(ValueError, match="Authenticated contribution calendar"):
        generate_stats(data_path, tmp_path)

    assert existing.read_text(encoding="utf-8") == "<svg/>"


def test_inconsistent_calendar_total_is_rejected():
    invalid = sample_data()
    invalid["stats"]["visible_contributions"] += 1
    with pytest.raises(ValueError, match="does not match"):
        render_cards(invalid)
