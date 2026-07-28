#!/usr/bin/env python3
"""Generate profile stats SVG from GitHub's GraphQL API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


GRAPHQL_QUERY = """
query userInfo($login: String!, $after: String, $startTime: DateTime = null) {
  user(login: $login) {
    name
    login
    commits: contributionsCollection(from: $startTime) {
      totalCommitContributions
    }
    reviews: contributionsCollection {
      totalPullRequestReviewContributions
    }
    repositoriesContributedTo(first: 1, contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]) {
      totalCount
    }
    pullRequests(first: 1) {
      totalCount
    }
    openIssues: issues(states: OPEN) {
      totalCount
    }
    closedIssues: issues(states: CLOSED) {
      totalCount
    }
    repositories(
      first: 100
      ownerAffiliations: OWNER
      orderBy: {direction: DESC, field: STARGAZERS}
      after: $after
    ) {
      totalCount
      nodes {
        name
        stargazers {
          totalCount
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""

REPOSITORIES_QUERY = """
query userRepositories($login: String!, $after: String) {
  user(login: $login) {
    repositories(
      first: 100
      ownerAffiliations: OWNER
      orderBy: {direction: DESC, field: STARGAZERS}
      after: $after
    ) {
      nodes {
        name
        stargazers {
          totalCount
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""

OUTPUT_PATH = Path(__file__).resolve().parents[2] / "profile" / "stats.svg"


def graphql_request(endpoint: str, token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Run a GitHub GraphQL request and return the user payload."""
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-stats-generator",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"GitHub GraphQL request failed with HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"GitHub GraphQL request failed: {error.reason}") from error

    errors = payload.get("errors")
    if errors:
        error = errors[0]
        message = error.get("message", "unknown GraphQL error")
        path = error.get("path")
        location = f" at {'.'.join(str(part) for part in path)}" if path else ""
        raise RuntimeError(f"GitHub GraphQL request failed{location}: {message}")

    user = payload.get("data", {}).get("user")
    if not user:
        raise RuntimeError("GitHub GraphQL request returned no user")
    return user


def fetch_stats(username: str, token: str, endpoint: str) -> dict[str, Any]:
    """Fetch the metrics displayed by the stats card."""
    start_time = datetime.now(timezone.utc) - timedelta(days=365)
    user = graphql_request(
        endpoint,
        token,
        GRAPHQL_QUERY,
        {
            "login": username,
            "after": None,
            "startTime": start_time.isoformat().replace("+00:00", "Z"),
        },
    )

    repositories = user["repositories"]
    total_stars = sum(repository["stargazers"]["totalCount"] for repository in repositories["nodes"])

    while repositories["pageInfo"]["hasNextPage"]:
        repositories = graphql_request(
            endpoint,
            token,
            REPOSITORIES_QUERY,
            {"login": username, "after": repositories["pageInfo"]["endCursor"]},
        )["repositories"]
        total_stars += sum(repository["stargazers"]["totalCount"] for repository in repositories["nodes"])

    total_issues = user["openIssues"]["totalCount"] + user["closedIssues"]["totalCount"]
    return {
        "name": user["name"] or user["login"],
        "stars": total_stars,
        "commits": user["commits"]["totalCommitContributions"],
        "prs": user["pullRequests"]["totalCount"],
        "reviews": user["reviews"]["totalPullRequestReviewContributions"],
        "issues": total_issues,
        "contribs": user["repositoriesContributedTo"]["totalCount"],
    }


def render_card(stats: dict[str, Any]) -> str:
    """Render a complete SVG card from the fetched statistics."""
    name = html.escape(str(stats["name"]), quote=False)
    description = (
        f"Total Stars Earned: {stats['stars']}, Total Commits  (last year) : {stats['commits']}, "
        f"Total PRs: {stats['prs']}, Total PRs Reviewed: {stats['reviews']}, "
        f"Total Issues: {stats['issues']}, Contributed to: {stats['contribs']}"
    )

    return f'''<svg
  width="320"
  height="220"
  viewBox="0 0 320 220"
  fill="none"
  xmlns="http://www.w3.org/2000/svg"
  role="img"
  aria-labelledby="descId"
>
  <title id="titleId">{name}'s GitHub Stats</title>
  <desc id="descId">{description}</desc>
  <style>
    .header {{
      font: 600 18px 'Segoe UI', Ubuntu, Sans-Serif;
      fill: #70a5fd;
    }}
    .stat {{
      font: 600 14px 'Segoe UI', Ubuntu, "Helvetica Neue", Sans-Serif;
      fill: #38bdae;
    }}
    .stagger {{
      opacity: 0;
      animation: fadeInAnimation 0.3s ease-in-out forwards;
    }}
    .bold {{ font-weight: 700; }}
    .icon {{ fill: #bf91f3; display: block; }}
    @keyframes fadeInAnimation {{
      from {{ opacity: 0; }}
      to {{ opacity: 1; }}
    }}
  </style>
  <rect
    data-testid="card-bg"
    x="0.5"
    y="0.5"
    rx="4.5"
    height="99%"
    stroke="#e4e2e2"
    width="319"
    fill="#1a1b27"
    stroke-opacity="1"
  />
  <g data-testid="card-title" transform="translate(25, 35)">
    <text class="header" data-testid="header" x="0" y="0">{name}'s GitHub Stats</text>
  </g>
  <g data-testid="main-card-body" transform="translate(0, 55)">
    <svg x="0" y="0">
{render_stat_rows(stats)}
    </svg>
  </g>
</svg>
'''


def render_stat_rows(stats: dict[str, Any]) -> str:
    """Render the six numeric statistics on the left side of the card."""
    rows = (
        (
            "stars",
            "Total Stars Earned:",
            "M8 .25a.75.75 0 01.673.418l1.882 3.815 4.21.612a.75.75 0 01.416 1.279l-3.046 2.97.719 4.192a.75.75 0 01-1.088.791L8 12.347l-3.766 1.98a.75.75 0 01-1.088-.79l.72-4.194L.818 6.374a.75.75 0 01.416-1.28l4.21-.611L7.327.668A.75.75 0 018 .25zm0 2.445L6.615 5.5a.75.75 0 01-.564.41l-3.097.45 2.24 2.184a.75.75 0 01.216.664l-.528 3.084 2.769-1.456a.75.75 0 01.698 0l2.77 1.456-.53-3.084a.75.75 0 01.216-.664l2.24-2.183-3.096-.45a.75.75 0 01-.564-.41L8 2.694v.001z",
        ),
        (
            "commits",
            "Total Commits (last year):",
            "M1.643 3.143L.427 1.927A.25.25 0 000 2.104V5.75c0 .138.112.25.25.25h3.646a.75.75 0 00.177-.427L2.715 4.215a6.5 6.5 0 11-1.18 4.458.75.75 0 10-1.493.154 8.001 8.001 0 101.6-5.684zM7.75 4a.75.75 0 01.75.75v2.992l2.028.812a.75.75 0 01-.557 1.392l-2.5-1A.75.75 0 017 8.25v-3.5A.75.75 0 017.75 4z",
        ),
        (
            "prs",
            "Total PRs:",
            "M7.177 3.073L9.573.677A.25.25 0 0110 .854v4.792a.25.25 0 01-.427.177L7.177 3.427a.25.25 0 010-.354zM3.75 2.5a.75.75 0 100 1.5.75.75 0 000-1.5zm-2.25.75a2.25 2.25 0 113 2.122v5.256a2.251 2.251 0 11-1.5 0V5.372A2.25 2.25 0 011.5 3.25zM11 2.5h-1V4h1a1 1 0 011 1v5.628a2.251 2.251 0 101.5 0V5A2.5 2.5 0 0011 2.5zm1 10.25a.75.75 0 111.5 0 .75.75 0 01-1.5 0zM3.75 12a.75.75 0 100 1.5.75.75 0 000-1.5z",
        ),
        (
            "reviews",
            "Total PRs Reviewed:",
            "M8 2c1.981 0 3.671.992 4.933 2.078 1.27 1.091 2.187 2.345 2.637 3.023a1.62 1.62 0 010 1.798c-.45.678-1.367 1.932-2.637 3.023C11.67 13.008 9.981 14 8 14c-1.981 0-3.671-.992-4.933-2.078C1.797 10.83.88 9.576.43 8.898a1.62 1.62 0 010-1.798c.45-.677 1.367-1.931 2.637-3.022C4.33 2.992 6.019 2 8 2ZM1.679 7.932a.12.12 0 000 .136c.411.622 1.241 1.75 2.366 2.717C5.176 11.758 6.527 12.5 8 12.5c1.473 0 2.825-.742 3.955-1.715 1.124-.967 1.954-2.096 2.366-2.717a.12.12 0 000-.136c-.412-.621-1.242-1.75-2.366-2.717C10.824 4.33 9.473 3.5 8 3.5c-1.473 0-2.825.742-3.955 1.715C2.92 6.182 2.09 7.311 1.679 7.932ZM8 10a2 2 0 1 1-.001-3.999A2 2 0 018 10Z",
        ),
        (
            "issues",
            "Total Issues:",
            "M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM0 8a8 8 0 1116 0A8 8 0 010 8zm9 3a1 1 0 11-2 0 1 1 0 012 0zm-.25-6.25a.75.75 0 00-1.5 0v3.5a.75.75 0 001.5 0v-3.5z",
        ),
        (
            "contribs",
            "Contributed to:",
            "M2 2.5A2.5 2.5 0 014.5 0h8.75a.75.75 0 01.75.75v12.5a.75.75 0 01-.75.75h-2.5a.75.75 0 110-1.5h1.75v-2h-8a1 1 0 00-.714 1.7.75.75 0 01-1.072 1.05A2.495 2.495 0 012 11.5v-9zm10.5-1V9h-8c-.356 0-.694.074-1 .208V2.5a1 1 0 011-1h8zM5 12.25v3.25a.25.25 0 00.4.2l1.45-1.087a.25.25 0 01.3 0L8.6 15.7a.25.25 0 00.4-.2v-3.25a.25.25 0 00-.25-.25h-3.5a.25.25 0 00-.25.25z",
        ),
    )
    rendered = []
    for index, (field, label, path) in enumerate(rows):
        y = index * 25
        delay = 450 + index * 150
        rendered.append(
            f'''      <g transform="translate(0, {y})">
        <g class="stagger" style="animation-delay: {delay}ms" transform="translate(25, 0)">
          <svg data-testid="icon" class="icon" viewBox="0 0 16 16" version="1.1" width="16" height="16">
            <path fill-rule="evenodd" d="{path}"/>
          </svg>
          <text class="stat bold" x="25" y="12.5">{label}</text>
          <text class="stat bold" x="224.01" y="12.5" data-testid="{field}">{stats[field]}</text>
        </g>
      </g>'''
        )
    return "\n".join(rendered)


def write_card(content: str) -> None:
    """Atomically replace the generated SVG."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=OUTPUT_PATH.parent,
        prefix=".stats-",
        suffix=".svg",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.replace(OUTPUT_PATH)


def main() -> int:
    """Fetch stats and update the profile card."""
    token = os.environ.get("GITHUB_TOKEN")
    username = os.environ.get("GITHUB_REPOSITORY_OWNER")
    if not token or not username:
        print("GITHUB_TOKEN and GITHUB_REPOSITORY_OWNER are required", file=sys.stderr)
        return 1

    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    endpoint = os.environ.get("GITHUB_GRAPHQL_URL", f"{api_url}/graphql")
    try:
        stats = fetch_stats(username, token, endpoint)
        write_card(render_card(stats))
    except (OSError, RuntimeError, StopIteration) as error:
        print(f"failed to generate stats card: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
