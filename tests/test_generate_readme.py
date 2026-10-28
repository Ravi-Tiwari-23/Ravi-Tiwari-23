from __future__ import annotations

import copy
from pathlib import Path

from scripts.fetch_github import load_config
from scripts.generate_readme import (
    build_context,
    markdown_escape,
    render_profile,
    select_featured_repositories,
    write_if_changed,
)
from scripts.validate_readme import validate_content


ROOT = Path(__file__).parents[1]


def repository(name: str, repo_id: int, **overrides):
    value = {
        "id": repo_id,
        "name": name,
        "full_name": f"Ravi-Tiwari-23/{name}",
        "description": f"Description for {name}",
        "url": f"https://github.com/Ravi-Tiwari-23/{name}",
        "homepage": "",
        "primary_language": "Python",
        "stars": 0,
        "forks": 0,
        "topics": [],
        "created_at": "2026-01-01T00:00:00Z",
        "pushed_at": "2026-10-01T00:00:00Z",
        "archived": False,
        "fork": False,
        "private": False,
        "languages": {"Python": 100},
    }
    value.update(overrides)
    return value


def config():
    return copy.deepcopy(load_config(ROOT / "config/profile.yaml"))


def data(repositories=None, *, snake=False):
    return {
        "repositories": repositories or [],
        "stats": {
            "public_repositories": len(repositories or []),
            "eligible_repositories": len(repositories or []),
            "total_stars": 0,
            "total_forks": 0,
            "public_commit_contributions": None,
            "public_pull_request_contributions": None,
        },
        "languages": [{"name": "Python", "percentage": 100.0, "bytes": 100}] if repositories else [],
        "activities": [],
        "verified_urls": {
            "live_project": True,
            "snake_light": snake,
            "snake_dark": snake,
            "project:estate_v2": True,
        },
    }


def test_repository_rename_keeps_curated_priority_by_stable_id():
    renamed = repository("genvilla-platform", 1346458827)
    selected = select_featured_repositories([renamed], config())
    assert selected[0]["display_title"] == "GenVilla / Your Home"
    assert selected[0]["name"] == "genvilla-platform"


def test_deleted_curated_repository_is_ignored():
    selected = select_featured_repositories([repository("unrelated", 999)], config())
    assert [item["name"] for item in selected] == ["unrelated"]
    assert not selected[0]["curated"]


def test_new_repository_fills_available_automatic_slot():
    selected = select_featured_repositories(
        [repository("Book-formatter", 1414693563), repository("new-api", 777)], config()
    )
    assert [item["name"] for item in selected] == ["Book-formatter", "new-api"]


def test_automatic_mode_ranks_without_curated_labels():
    settings = config()
    settings["featured_projects"]["mode"] = "automatic"
    selected = select_featured_repositories([repository("Book-formatter", 1414693563)], settings)
    assert selected[0]["curated"] is False
    assert selected[0]["display_title"] == "Book-formatter"


def test_curated_mode_does_not_fill_with_unconfigured_repositories():
    settings = config()
    settings["featured_projects"]["mode"] = "curated"
    selected = select_featured_repositories([repository("unrelated", 999)], settings)
    assert selected == []


def test_markdown_special_characters_are_escaped():
    assert markdown_escape("API [v2] *beta* | safe") == r"API \[v2\] \*beta\* \| safe"


def test_unchanged_generation_does_not_rewrite_file(tmp_path):
    output = tmp_path / "README.md"
    output.write_text("same\n", encoding="utf-8")
    assert write_if_changed(output, "same\n") is False
    assert write_if_changed(output, "changed\n") is True
    assert write_if_changed(output, "changed\n") is False


def test_missing_graphics_are_not_referenced():
    rendered = render_profile(ROOT / "templates/profile.md.j2", config(), data())
    assert "output/github-contribution-grid-snake" not in rendered


def test_available_graphics_render_light_and_dark_sources():
    rendered = render_profile(ROOT / "templates/profile.md.j2", config(), data(snake=True))
    assert "github-contribution-grid-snake.svg" in rendered
    assert "github-contribution-grid-snake-dark.svg" in rendered


def test_profile_supports_light_and_dark_theme_sources():
    rendered = render_profile(ROOT / "templates/profile.md.j2", config(), data())
    assert rendered.count('media="(prefers-color-scheme: dark)"') >= 3
    assert "title_color=2563EB" in rendered


def test_generated_profile_passes_validation():
    rendered = render_profile(
        ROOT / "templates/profile.md.j2",
        config(),
        data([repository("Book-formatter", 1414693563)]),
    )
    assert validate_content(rendered, ROOT) == []


def test_build_context_handles_missing_optional_api_fields():
    context = build_context(config(), {"repositories": []})
    assert context["github"]["stats"]["total_stars"] == 0
    assert context["snake_available"] is False
