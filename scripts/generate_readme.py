from __future__ import annotations

import argparse
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

try:
    from .fetch_github import load_config, repository_is_eligible
    from .validate_readme import validate_content
except ImportError:
    from fetch_github import load_config, repository_is_eligible
    from validate_readme import validate_content


def markdown_escape(value: Any) -> str:
    text = str(value or "")
    return re.sub(r"([\\`*_{}\[\]()<>#+|])", r"\\\1", text)


def inline_code(value: Any) -> str:
    return f"`{str(value).replace('`', '')}`"


def display_date(value: str) -> str:
    if not value:
        return "Unknown"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d %b %Y")
    except ValueError:
        return value[:10]


def repository_score(repo: dict[str, Any]) -> float:
    pushed = repo.get("pushed_at", "")
    recency = 0.0
    if pushed:
        try:
            age = max((datetime.now(UTC) - datetime.fromisoformat(pushed.replace("Z", "+00:00"))).days, 0)
            recency = max(0.0, 25.0 - age / 30)
        except ValueError:
            pass
    description_score = 8 if repo.get("description") != "No public description provided." else 0
    homepage_score = 10 if repo.get("homepage") else 0
    topic_score = min(len(repo.get("topics") or []) * 2, 8)
    community_score = math.log2(repo.get("stars", 0) + 1) * 6 + math.log2(repo.get("forks", 0) + 1) * 3
    return round(recency + description_score + homepage_score + topic_score + community_score, 2)


def select_featured_repositories(
    repositories: list[dict[str, Any]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    settings = config.get("featured_projects", {})
    mode = str(settings.get("mode", "hybrid")).casefold()
    if mode not in {"automatic", "curated", "hybrid"}:
        raise ValueError("featured_projects.mode must be automatic, curated, or hybrid")
    maximum = int(settings.get("maximum", 4))
    eligible = [repo for repo in repositories if repository_is_eligible(repo, config)]
    by_id = {repo.get("id"): repo for repo in eligible if repo.get("id") is not None}
    by_name = {repo.get("name", "").casefold(): repo for repo in eligible}
    selected: list[dict[str, Any]] = []
    selected_ids: set[Any] = set()

    curated_projects = settings.get("projects", []) if mode in {"curated", "hybrid"} else []
    for project in sorted(curated_projects, key=lambda item: item.get("priority", 0), reverse=True):
        repo = by_id.get(project.get("repository_id")) or by_name.get(str(project.get("repository", "")).casefold())
        if not repo or repo.get("id") in selected_ids:
            continue
        card = dict(repo)
        card.update(
            {
                "display_title": project.get("display_title") or repo["name"],
                "category": project.get("category") or "Software Project",
                "short_description": project.get("short_description") or repo["description"],
                "status": project.get("status") or "",
                "tech_stack": project.get("tech_stack") or [repo["primary_language"]],
                "demo_url": project.get("demo_url") or "",
                "curated": True,
                "verification_key": f"project:{project.get('repository')}",
                "score": repository_score(repo) + float(project.get("priority", 0)),
            }
        )
        selected.append(card)
        selected_ids.add(repo.get("id"))
        if len(selected) >= maximum:
            return selected

    if mode == "curated":
        return selected

    automatic = []
    for repo in eligible:
        if repo.get("id") in selected_ids:
            continue
        card = dict(repo)
        card.update(
            {
                "display_title": repo["name"],
                "category": "Public Repository",
                "short_description": repo["description"],
                "status": "",
                "tech_stack": [repo["primary_language"]] if repo["primary_language"] != "Not specified" else [],
                "demo_url": repo.get("homepage", ""),
                "curated": False,
                "verification_key": f"repo:{repo['name']}",
                "score": repository_score(repo),
            }
        )
        automatic.append(card)
    automatic.sort(key=lambda item: (-item["score"], item["name"].casefold()))
    selected.extend(automatic[: max(0, maximum - len(selected))])
    return selected


def build_context(config: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    github = dict(data)
    stats = dict(github.get("stats", {}))
    for key, default in {
        "public_repositories": 0,
        "eligible_repositories": 0,
        "total_stars": 0,
        "total_forks": 0,
        "public_commit_contributions": None,
        "public_pull_request_contributions": None,
        "visible_contributions": None,
        "restricted_contributions": None,
        "issue_contributions": None,
        "review_contributions": None,
    }.items():
        stats.setdefault(key, default)
    github["stats"] = stats
    github.setdefault(
        "reporting_period",
        {
            "start": "Unavailable",
            "end": "Unavailable",
            "days": config.get("statistics", {}).get("period_days", 365),
            "timezone": config.get("statistics", {}).get("timezone", "Asia/Kolkata"),
        },
    )
    github.setdefault("languages", [])
    github.setdefault("activities", [])
    verified = {
        "live_project": False,
        "snake_light": False,
        "snake_dark": False,
        **github.get("verified_urls", {}),
    }
    github["verified_urls"] = verified
    featured = select_featured_repositories(github.get("repositories", []), config)
    for project in featured:
        if project.get("demo_url") and not verified.get(project["verification_key"], False):
            project["demo_url"] = ""
    return {
        **config,
        "github": github,
        "featured": featured,
        "snake_available": bool(verified.get("snake_light") and verified.get("snake_dark")),
    }


def render_profile(template_path: Path, config: dict[str, Any], data: dict[str, Any]) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["md"] = markdown_escape
    environment.filters["code"] = inline_code
    environment.filters["date"] = display_date
    template = environment.get_template(template_path.name)
    return template.render(**build_context(config, data)).rstrip() + "\n"


def write_if_changed(path: Path, content: str) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    if existing == content:
        return False
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)
    return True


def generate(
    *,
    config_path: Path,
    data_path: Path,
    template_path: Path,
    output_path: Path,
    repository_root: Path,
) -> bool:
    config = load_config(config_path)
    data = json.loads(data_path.read_text(encoding="utf-8"))
    content = render_profile(template_path, config, data)
    errors = validate_content(content, repository_root)
    if errors:
        raise ValueError("Generated README failed validation:\n- " + "\n- ".join(errors))
    return write_if_changed(output_path, content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render README.md from verified GitHub data.")
    parser.add_argument("--config", type=Path, default=Path("config/profile.yaml"))
    parser.add_argument("--data", type=Path, default=Path(".profile-data.json"))
    parser.add_argument("--template", type=Path, default=Path("templates/profile.md.j2"))
    parser.add_argument("--output", type=Path, default=Path("README.md"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        changed = generate(
            config_path=args.config,
            data_path=args.data,
            template_path=args.template,
            output_path=args.output,
            repository_root=Path(".").resolve(),
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"README generation failed safely: {error}")
        return 1
    print("README.md updated." if changed else "README.md is already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
