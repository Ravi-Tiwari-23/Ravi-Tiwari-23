from __future__ import annotations

import copy
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from scripts.fetch_github import (
    GitHubAPIError,
    GitHubClient,
    aggregate_languages,
    collect_profile_data,
    fetch_to_file,
    load_config,
    merge_pull_request_activity,
    normalize_repository,
    repository_is_eligible,
    summarize_events,
)


ROOT = Path(__file__).parents[1]


def raw_repo(name: str, repo_id: int = 1, **overrides):
    value = {
        "id": repo_id,
        "name": name,
        "full_name": f"Ravi-Tiwari-23/{name}",
        "description": f"Description for {name}",
        "html_url": f"https://github.com/Ravi-Tiwari-23/{name}",
        "homepage": "",
        "language": "Python",
        "stargazers_count": 0,
        "forks_count": 0,
        "topics": [],
        "created_at": "2026-01-01T00:00:00Z",
        "pushed_at": "2026-10-01T00:00:00Z",
        "archived": False,
        "fork": False,
        "private": False,
    }
    value.update(overrides)
    return value


class FakeClient:
    token = None
    timeout = 1

    def __init__(self, repositories=None, events=None, languages=None):
        self.repositories = repositories or []
        self.events = events or []
        self.languages = languages or {}

    def request_json(self, path, **_kwargs):
        if path.startswith("/users/"):
            return {
                "login": "Ravi-Tiwari-23",
                "html_url": "https://github.com/Ravi-Tiwari-23",
                "public_repos": len([repo for repo in self.repositories if not repo.get("private")]),
            }, {}
        if path.endswith("/languages"):
            name = path.split("/")[-2]
            return self.languages.get(name, {}), {}
        raise AssertionError(f"Unexpected request: {path}")

    def paginate(self, path, **_kwargs):
        if "/repos?" in path:
            return self.repositories
        if "/events/public" in path:
            return self.events
        raise AssertionError(f"Unexpected pagination path: {path}")


@pytest.fixture
def config():
    value = load_config(ROOT / "config/profile.yaml")
    value = copy.deepcopy(value)
    value["update"]["verify_external_urls"] = False
    return value


def test_account_with_no_public_repositories(config):
    data = collect_profile_data(FakeClient(), config)
    assert data["repositories"] == []
    assert data["languages"] == []
    assert data["stats"]["eligible_repositories"] == 0


def test_new_public_repository_is_discovered(config):
    client = FakeClient([raw_repo("new-service")], languages={"new-service": {"Python": 900}})
    data = collect_profile_data(client, config)
    assert [repo["name"] for repo in data["eligible_repositories"]] == ["new-service"]
    assert data["languages"] == [{"name": "Python", "bytes": 900, "percentage": 100.0}]


def test_private_archived_and_forked_repositories_are_excluded(config):
    values = [
        normalize_repository(raw_repo("private", private=True)),
        normalize_repository(raw_repo("archived", archived=True)),
        normalize_repository(raw_repo("fork", fork=True)),
    ]
    assert not any(repository_is_eligible(repo, config) for repo in values)


def test_private_repository_cannot_be_force_included(config):
    config["repository_rules"]["include"] = ["private"]
    private = normalize_repository(raw_repo("private", private=True))
    assert repository_is_eligible(private, config) is False


def test_missing_description_and_language_are_safe(config):
    client = FakeClient([raw_repo("blank", description=None, language=None)])
    data = collect_profile_data(client, config)
    assert data["repositories"][0]["description"] == "No public description provided."
    assert data["repositories"][0]["primary_language"] == "Not specified"
    assert data["languages"] == []


def test_language_aggregation_handles_empty_metadata():
    assert aggregate_languages([{"languages": {}}, {}]) == []


def test_recent_activity_deduplicates_pushes_and_excludes_profile_repo():
    events = [
        {"type": "PushEvent", "repo": {"name": "Ravi-Tiwari-23/api"}, "payload": {"size": 2}, "created_at": "2026-10-02T00:00:00Z"},
        {"type": "PushEvent", "repo": {"name": "Ravi-Tiwari-23/api"}, "payload": {"size": 1}, "created_at": "2026-10-01T00:00:00Z"},
        {"type": "PushEvent", "repo": {"name": "Ravi-Tiwari-23/Ravi-Tiwari-23"}, "payload": {"size": 1}, "created_at": "2026-10-03T00:00:00Z"},
    ]
    activities = summarize_events(events, profile_repo="Ravi-Tiwari-23", maximum=5)
    assert len(activities) == 1
    assert activities[0]["detail"] == "2 public commits to api"


def test_graphql_pull_requests_exclude_private_repositories():
    pulls = [
        {
            "title": "Public improvement",
            "url": "https://github.com/example/public/pull/1",
            "createdAt": "2026-10-20T00:00:00Z",
            "state": "MERGED",
            "repository": {"nameWithOwner": "example/public", "isPrivate": False},
        },
        {
            "title": "Secret change",
            "url": "https://github.com/example/private/pull/1",
            "createdAt": "2026-10-21T00:00:00Z",
            "state": "OPEN",
            "repository": {"nameWithOwner": "example/private", "isPrivate": True},
        },
    ]
    activities = merge_pull_request_activity([], pulls, 5)
    assert [item["detail"] for item in activities] == ["Public improvement"]
    assert activities[0]["action"] == "Merged a pull request"


def test_graphql_contribution_totals_are_collected(config):
    class TokenClient(FakeClient):
        token = "mock-token"

        def __init__(self):
            super().__init__()
            self.variables = None

        def graphql(self, _query, variables):
            self.variables = variables
            return {
                "user": {
                    "contributionsCollection": {
                        "totalCommitContributions": 12,
                        "totalPullRequestContributions": 3,
                        "totalIssueContributions": 2,
                        "totalPullRequestReviewContributions": 4,
                        "restrictedContributionsCount": 5,
                        "contributionCalendar": {
                            "totalContributions": 1,
                            "weeks": [{"contributionDays": []}],
                        },
                    },
                    "pullRequests": {"nodes": []},
                }
            }

    client = TokenClient()
    result = collect_profile_data(client, config)
    assert result["stats"]["public_commit_contributions"] == 12
    assert result["stats"]["public_pull_request_contributions"] == 3
    assert result["stats"]["restricted_contributions"] == 5
    assert result["reporting_period"]["days"] == 365
    assert client.variables["login"] == "Ravi-Tiwari-23"
    assert client.variables["from"] < client.variables["to"]


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload
        self.headers = Message()

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_rate_limit_response_retries_without_exposing_token():
    headers = Message()
    headers["X-RateLimit-Remaining"] = "0"
    headers["Retry-After"] = "0"
    responses = [
        HTTPError("https://api.github.com/user", 403, "rate limited", headers, None),
        FakeResponse({"login": "Ravi-Tiwari-23"}),
    ]
    sleeps = []

    def opener(*_args, **_kwargs):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    client = GitHubClient("secret-value", retries=1, opener=opener, sleep=sleeps.append)
    payload, _ = client.request_json("/user")
    assert payload["login"] == "Ravi-Tiwari-23"
    assert sleeps == [0.0]


def test_api_failure_preserves_last_successful_output(config, tmp_path):
    output = tmp_path / "profile.json"
    output.write_text('{"valid": true}\n', encoding="utf-8")

    class FailingClient(FakeClient):
        def request_json(self, *_args, **_kwargs):
            raise GitHubAPIError("temporary outage")

    with pytest.raises(GitHubAPIError):
        fetch_to_file(FailingClient(), config, output)
    assert output.read_text(encoding="utf-8") == '{"valid": true}\n'


def test_network_unavailability_raises_after_retries():
    attempts = []

    def opener(*_args, **_kwargs):
        attempts.append(1)
        raise URLError("offline")

    client = GitHubClient(retries=2, opener=opener, sleep=lambda _delay: None)
    with pytest.raises(GitHubAPIError, match="temporarily unavailable"):
        client.request_json("/user")
    assert len(attempts) == 3
