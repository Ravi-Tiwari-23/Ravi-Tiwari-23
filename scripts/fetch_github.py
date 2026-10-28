from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import yaml


RETRIABLE_STATUS_CODES = {429, 500, 502, 503, 504}
GRAPHQL_QUERY = """
query ProfileSummary($login: String!) {
  user(login: $login) {
    contributionsCollection {
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      restrictedContributionsCount
    }
    pullRequests(first: 5, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        title
        url
        createdAt
        state
        repository { nameWithOwner isPrivate }
      }
    }
  }
}
"""


class GitHubAPIError(RuntimeError):
    """Raised when GitHub data cannot be retrieved safely."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: int = 20,
        retries: int = 3,
        opener: Callable[..., Any] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
        api_url: str = "https://api.github.com",
    ) -> None:
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.opener = opener
        self.sleep = sleep
        self.api_url = api_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Ravi-Tiwari-23-profile-generator",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request_json(
        self,
        path_or_url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        url = path_or_url if path_or_url.startswith("http") else f"{self.api_url}{path_or_url}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            request = Request(url, data=body, method=method, headers=self._headers())
            if body is not None:
                request.add_header("Content-Type", "application/json")
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw), dict(response.headers.items())
            except HTTPError as error:
                last_error = error
                status = error.code
                headers = dict(error.headers.items()) if error.headers else {}
                rate_limited = status == 403 and headers.get("X-RateLimit-Remaining") == "0"
                if attempt < self.retries and (status in RETRIABLE_STATUS_CODES or rate_limited):
                    self.sleep(self._retry_delay(attempt, headers))
                    continue
                raise GitHubAPIError(f"GitHub API request failed with HTTP {status}", status) from error
            except (URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt < self.retries:
                    self.sleep(min(2**attempt + random.random(), 8))
                    continue
                raise GitHubAPIError("GitHub API is temporarily unavailable") from error

        raise GitHubAPIError("GitHub API request failed") from last_error

    @staticmethod
    def _retry_delay(attempt: int, headers: dict[str, str]) -> float:
        retry_after = headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 60)
        reset = headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit():
            return max(1, min(float(reset) - time.time(), 60))
        return min(2**attempt + random.random(), 8)

    def paginate(self, path: str, *, max_pages: int = 10) -> list[Any]:
        url = path if path.startswith("http") else f"{self.api_url}{path}"
        items: list[Any] = []
        pages = 0
        while url and pages < max_pages:
            payload, headers = self.request_json(url)
            if not isinstance(payload, list):
                raise GitHubAPIError("Expected a paginated list from GitHub")
            items.extend(payload)
            url = _next_link(headers.get("Link", ""))
            pages += 1
        return items

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if not self.token:
            return {}
        payload, _ = self.request_json(
            "/graphql", method="POST", payload={"query": query, "variables": variables}
        )
        if payload.get("errors"):
            raise GitHubAPIError("GitHub GraphQL returned an error")
        return payload.get("data", {})


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        match = re.match(r'\s*<([^>]+)>;\s*rel="([^"]+)"', part)
        if match and match.group(2) == "next":
            return match.group(1)
    return None


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("Profile configuration must be a YAML mapping")
    return config


def repository_is_eligible(repo: dict[str, Any], config: dict[str, Any]) -> bool:
    rules = config.get("repository_rules", {})
    name = repo.get("name", "")
    if repo.get("private"):
        return False
    forced = set(rules.get("include", []))
    if name in forced:
        return True
    if name in set(rules.get("exclude", [])):
        return False
    if rules.get("exclude_profile_repository", True) and name.casefold() == config["profile"]["username"].casefold():
        return False
    if rules.get("exclude_archived", True) and repo.get("archived"):
        return False
    if rules.get("exclude_forks", True) and repo.get("fork"):
        return False
    return True


def normalize_repository(repo: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": repo.get("id"),
        "name": repo.get("name", ""),
        "full_name": repo.get("full_name", ""),
        "description": repo.get("description") or "No public description provided.",
        "url": repo.get("html_url", ""),
        "homepage": repo.get("homepage") or "",
        "primary_language": repo.get("language") or "Not specified",
        "stars": int(repo.get("stargazers_count") or 0),
        "forks": int(repo.get("forks_count") or 0),
        "topics": list(repo.get("topics") or []),
        "created_at": repo.get("created_at", ""),
        "pushed_at": repo.get("pushed_at", ""),
        "archived": bool(repo.get("archived")),
        "fork": bool(repo.get("fork")),
        "private": bool(repo.get("private")),
        "languages": {},
    }


def is_safe_public_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username


def probe_url(url: str, *, timeout: int = 12, opener: Callable[..., Any] = urlopen) -> bool:
    if not is_safe_public_url(url):
        return False
    for method in ("HEAD", "GET"):
        try:
            request = Request(url, method=method, headers={"User-Agent": "Ravi-profile-link-check"})
            with opener(request, timeout=timeout) as response:
                return 200 <= int(response.status) < 400
        except HTTPError as error:
            if method == "HEAD" and error.code in {403, 405}:
                continue
            return False
        except (URLError, TimeoutError, OSError):
            return False
    return False


def summarize_events(
    events: Iterable[dict[str, Any]],
    *,
    profile_repo: str,
    maximum: int,
    excluded_repositories: Iterable[str] = (),
) -> list[dict[str, str]]:
    activities: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    excluded = {value.casefold() for value in excluded_repositories}

    for event in events:
        event_type = event.get("type", "")
        repo_name = event.get("repo", {}).get("name", "")
        short_repo = repo_name.split("/")[-1]
        if (
            not repo_name
            or short_repo.casefold() == profile_repo.casefold()
            or short_repo.casefold() in excluded
            or repo_name.casefold() in excluded
        ):
            continue
        payload = event.get("payload") or {}
        action = ""
        detail = ""
        url = f"https://github.com/{repo_name}"

        if event_type == "PushEvent":
            commits = payload.get("commits") or []
            count = len(commits) or int(payload.get("size") or 0)
            action = "Pushed commits"
            detail = f"{count} public commit{'s' if count != 1 else ''} to {short_repo}"
        elif event_type == "PullRequestEvent":
            pull = payload.get("pull_request") or {}
            verb = "Merged" if pull.get("merged") else str(payload.get("action", "Updated")).title()
            action = f"{verb} a pull request"
            detail = pull.get("title") or short_repo
            url = pull.get("html_url") or url
        elif event_type == "ReleaseEvent":
            release = payload.get("release") or {}
            action = "Published a release"
            detail = f"{release.get('tag_name', 'release')} in {short_repo}"
            url = release.get("html_url") or url
        elif event_type == "CreateEvent" and payload.get("ref_type") == "repository":
            action = "Created a repository"
            detail = short_repo
        elif event_type == "PublicEvent":
            action = "Made a repository public"
            detail = short_repo
        else:
            continue

        key = (event_type, repo_name)
        if key in seen:
            continue
        seen.add(key)
        activities.append(
            {
                "action": action,
                "detail": detail,
                "url": url,
                "date": str(event.get("created_at", ""))[:10],
            }
        )
        if len(activities) >= maximum:
            break
    return activities


def aggregate_languages(repositories: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    for repo in repositories:
        for language, count in repo.get("languages", {}).items():
            totals[language] = totals.get(language, 0) + int(count)
    overall = sum(totals.values())
    if not overall:
        return []
    ranked = [
        {"name": name, "bytes": count, "percentage": round(count * 100 / overall, 1)}
        for name, count in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    ]
    return [language for language in ranked if language["percentage"] >= 0.1][:6]


def merge_pull_request_activity(
    activities: list[dict[str, str]], pull_requests: Iterable[dict[str, Any]], maximum: int
) -> list[dict[str, str]]:
    combined = list(activities)
    seen_urls = {activity.get("url", "") for activity in combined}
    for pull in pull_requests:
        repository = pull.get("repository") or {}
        if repository.get("isPrivate") or not pull.get("url") or pull["url"] in seen_urls:
            continue
        state = str(pull.get("state", "")).upper()
        action = "Merged a pull request" if state == "MERGED" else "Opened a pull request"
        combined.append(
            {
                "action": action,
                "detail": pull.get("title") or repository.get("nameWithOwner", "public repository"),
                "url": pull["url"],
                "date": str(pull.get("createdAt", ""))[:10],
            }
        )
        seen_urls.add(pull["url"])
    combined.sort(key=lambda item: item.get("date", ""), reverse=True)
    return combined[:maximum]


def collect_profile_data(
    client: GitHubClient,
    config: dict[str, Any],
    *,
    url_probe: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    username = config["profile"]["username"]
    user, _ = client.request_json(f"/users/{username}")
    raw_repos = client.paginate(
        f"/users/{username}/repos?{urlencode({'per_page': 100, 'type': 'owner', 'sort': 'updated'})}"
    )
    repositories = [normalize_repository(repo) for repo in raw_repos]
    eligible = [repo for repo in repositories if repository_is_eligible(repo, config)]

    for repo in eligible:
        languages, _ = client.request_json(f"/repos/{username}/{repo['name']}/languages")
        repo["languages"] = languages if isinstance(languages, dict) else {}

    events = client.paginate(f"/users/{username}/events/public?per_page=100", max_pages=3)
    activities = summarize_events(
        events,
        profile_repo=username,
        maximum=int(config.get("activity", {}).get("maximum", 5)),
        excluded_repositories=config.get("activity", {}).get("exclude_repositories", []),
    )

    graphql: dict[str, Any] = {}
    if client.token:
        graphql = client.graphql(GRAPHQL_QUERY, {"login": username}).get("user") or {}

    verified_urls: dict[str, bool] = {}
    if config.get("update", {}).get("verify_external_urls", True):
        checker = url_probe or (lambda value: probe_url(value, timeout=client.timeout))
        candidates = {
            key: value
            for key, value in config.get("links", {}).items()
            if key in {"live_project", "snake_light", "snake_dark"} and value
        }
        for repo in eligible:
            if repo.get("homepage"):
                candidates[f"repo:{repo['name']}"] = repo["homepage"]
        for project in config.get("featured_projects", {}).get("projects", []):
            if project.get("demo_url"):
                candidates[f"project:{project.get('repository')}"] = project["demo_url"]
        verified_urls = {key: checker(url) for key, url in candidates.items()}

    contribution_data = (graphql.get("contributionsCollection") or {}) if graphql else {}
    public_pull_requests = [
        pull
        for pull in ((graphql.get("pullRequests") or {}).get("nodes", []) if graphql else [])
        if not (pull.get("repository") or {}).get("isPrivate")
    ]
    activities = merge_pull_request_activity(
        activities,
        public_pull_requests,
        int(config.get("activity", {}).get("maximum", 5)),
    )
    stats = {
        "public_repositories": int(user.get("public_repos") or 0),
        "eligible_repositories": len(eligible),
        "total_stars": sum(repo["stars"] for repo in eligible),
        "total_forks": sum(repo["forks"] for repo in eligible),
        "public_commit_contributions": contribution_data.get("totalCommitContributions"),
        "public_pull_request_contributions": contribution_data.get("totalPullRequestContributions"),
    }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "user": {
            "login": user.get("login", username),
            "url": user.get("html_url", f"https://github.com/{username}"),
            "avatar_url": user.get("avatar_url", ""),
        },
        "repositories": repositories,
        "eligible_repositories": eligible,
        "stats": stats,
        "languages": aggregate_languages(eligible),
        "activities": activities,
        "recent_pull_requests": public_pull_requests,
        "verified_urls": verified_urls,
    }


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def fetch_to_file(client: GitHubClient, config: dict[str, Any], output: Path) -> dict[str, Any]:
    data = collect_profile_data(client, config)
    write_json_atomic(output, data)
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch public GitHub profile data safely.")
    parser.add_argument("--config", type=Path, default=Path("config/profile.yaml"))
    parser.add_argument("--output", type=Path, default=Path(".profile-data.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    update = config.get("update", {})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    client = GitHubClient(
        token,
        timeout=int(update.get("api_timeout_seconds", 20)),
        retries=int(update.get("api_retries", 3)),
    )
    try:
        data = fetch_to_file(client, config, args.output)
    except (GitHubAPIError, ValueError) as error:
        print(f"Profile data refresh failed safely: {error}", file=os.sys.stderr)
        return 1
    print(
        f"Fetched {len(data['repositories'])} public repositories and "
        f"{len(data['activities'])} recent activities."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
