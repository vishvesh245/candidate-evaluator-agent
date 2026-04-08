import httpx
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
from models.schemas import GitHubSignals
from config import settings

GITHUB_API = "https://api.github.com"
TIMEOUT = 10.0


def get_headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def extract_username(github_url: str) -> Optional[str]:
    """Extract username from various GitHub URL formats."""
    patterns = [
        r"github\.com/([a-zA-Z0-9_-]+)/?$",
        r"github\.com/([a-zA-Z0-9_-]+)/?(?:\?|#|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, github_url.rstrip("/"))
        if match:
            username = match.group(1)
            # Skip if it looks like a repo path (has more segments)
            if "/" not in github_url.split("github.com/")[-1].rstrip("/"):
                return username
    return None


async def extract_github_signals(github_url: str) -> GitHubSignals:
    username = extract_username(github_url)
    if not username:
        return GitHubSignals(
            username="unknown",
            error="Could not parse GitHub username from the provided URL.",
        )

    headers = get_headers()

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # Fetch user profile
        user_resp = await client.get(f"{GITHUB_API}/users/{username}", headers=headers)

        if user_resp.status_code == 404:
            return GitHubSignals(
                username=username,
                error=f"GitHub profile '{username}' not found. Please check the URL.",
            )
        if user_resp.status_code == 403:
            return GitHubSignals(
                username=username,
                error="GitHub API rate limit hit. Evaluation will proceed without GitHub signals.",
            )
        if user_resp.status_code != 200:
            return GitHubSignals(
                username=username,
                error=f"Could not fetch GitHub profile (status {user_resp.status_code}).",
            )

        user_data = user_resp.json()

        # Calculate account age
        created_at = user_data.get("created_at", "")
        account_age_days = 0
        if created_at:
            try:
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                account_age_days = (datetime.now(timezone.utc) - created).days
            except Exception:
                pass

        # Fetch repos
        repos_resp = await client.get(
            f"{GITHUB_API}/users/{username}/repos",
            headers=headers,
            params={"sort": "pushed", "per_page": 30, "type": "owner"},
        )
        repos = repos_resp.json() if repos_resp.status_code == 200 else []
        if not isinstance(repos, list):
            repos = []

        # Aggregate languages
        language_counts: dict[str, int] = {}
        notable_repos = []
        for repo in repos:
            if repo.get("fork"):
                continue  # Skip forks for language signal
            lang = repo.get("language")
            if lang:
                language_counts[lang] = language_counts.get(lang, 0) + 1

            # Notable repos: has description OR stars > 0
            if repo.get("description") or (repo.get("stargazers_count", 0) > 0):
                notable_repos.append(
                    {
                        "name": repo.get("name"),
                        "description": repo.get("description"),
                        "stars": repo.get("stargazers_count", 0),
                        "language": repo.get("language"),
                        "pushed_at": repo.get("pushed_at", ""),
                    }
                )

        top_languages = sorted(language_counts, key=language_counts.get, reverse=True)[:5]

        # Fetch recent public events (activity in last 90 days)
        events_resp = await client.get(
            f"{GITHUB_API}/users/{username}/events/public",
            headers=headers,
            params={"per_page": 100},
        )
        events = events_resp.json() if events_resp.status_code == 200 else []
        if not isinstance(events, list):
            events = []

        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        recent_activity = 0
        for event in events:
            created = event.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if dt >= cutoff:
                        recent_activity += 1
                except Exception:
                    pass

        return GitHubSignals(
            username=username,
            bio=user_data.get("bio"),
            public_repos=user_data.get("public_repos", 0),
            followers=user_data.get("followers", 0),
            account_age_days=account_age_days,
            top_languages=top_languages,
            recent_activity_count=recent_activity,
            has_pinned_repos=False,  # Requires GraphQL; skip for now
            notable_repos=notable_repos[:8],  # Cap at 8
        )
