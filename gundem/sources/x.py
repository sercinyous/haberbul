"""Read-only X API v2 client. Never writes to X."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from gundem.budget import XBudget

log = logging.getLogger(__name__)

BASE_URL = "https://api.x.com/2"
MAX_QUERY_LEN = 512
_SUFFIX = " -is:retweet -is:reply (lang:tr OR lang:en)"


@dataclass
class XTweet:
    id: str
    author_handle: str | None
    text: str
    created_at: datetime
    like_count: int
    repost_count: int
    reply_count: int
    quote_count: int


def _group(items: list[str]) -> str:
    return "(" + " OR ".join(items) + ")" + _SUFFIX


def build_query(terms: list[str], from_handles: list[str] = []) -> str:
    if bool(terms) == bool(from_handles):
        raise ValueError("give exactly one of terms or from_handles")
    items = terms if terms else [f"from:{h}" for h in from_handles]
    query = _group(items)
    if len(query) > MAX_QUERY_LEN:
        raise ValueError(f"query is {len(query)} chars, limit {MAX_QUERY_LEN}")
    return query


def build_queries(terms: list[str] = [], from_handles: list[str] = []) -> list[str]:
    """Like build_query, but splits the list so each query fits in 512 chars."""
    if bool(terms) == bool(from_handles):
        raise ValueError("give exactly one of terms or from_handles")
    items = terms if terms else [f"from:{h}" for h in from_handles]
    queries: list[str] = []
    chunk: list[str] = []
    for item in items:
        if len(_group([item])) > MAX_QUERY_LEN:
            raise ValueError(f"single item too long: {item[:40]}")
        if chunk and len(_group(chunk + [item])) > MAX_QUERY_LEN:
            queries.append(_group(chunk))
            chunk = []
        chunk.append(item)
    queries.append(_group(chunk))
    return queries


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class XClient:
    def __init__(self, bearer: str, budget: XBudget, http: httpx.Client):
        self.bearer = bearer
        self.budget = budget
        self.http = http

    def search_recent(
        self, job: str, query: str, since_id: str | None = None, max_results: int = 10
    ) -> tuple[list[XTweet], str | None]:
        if not self.budget.try_reserve(job, max_results):
            log.info("x budget exhausted for job %s; skipping call", job)
            return [], None
        params = {
            "query": query,
            "max_results": max_results,
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id",
            "user.fields": "username",
        }
        if since_id:
            params["since_id"] = since_id
        try:
            resp = self.http.get(
                f"{BASE_URL}/tweets/search/recent",
                params=params,
                headers={"Authorization": f"Bearer {self.bearer}"},
            )
        except httpx.HTTPError as exc:
            log.warning("x request failed: %s", exc)
            self.budget.refund(job, max_results)
            return [], None
        if resp.status_code != 200:
            log.warning("x search returned %s", resp.status_code)
            self.budget.refund(job, max_results)
            return [], None
        try:
            body = resp.json()
            tweets = self._parse(body)
            meta = body.get("meta", {})
            used = min(meta.get("result_count", len(tweets)), max_results)
            newest_id = meta.get("newest_id")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            # X billed this call; keep the full reservation since the read count is unknown.
            log.warning("x response unparseable, keeping reservation: %s", exc)
            return [], None
        self.budget.refund(job, max_results - used)
        return tweets, newest_id

    @staticmethod
    def _parse(body: dict) -> list[XTweet]:
        users = {u["id"]: u["username"] for u in body.get("includes", {}).get("users", [])}
        tweets = []
        for d in body.get("data", []):
            m = d.get("public_metrics", {})
            tweets.append(
                XTweet(
                    id=d["id"],
                    author_handle=users.get(d.get("author_id")),
                    text=d["text"],
                    created_at=_parse_time(d["created_at"]),
                    like_count=m.get("like_count", 0),
                    repost_count=m.get("retweet_count", 0),
                    reply_count=m.get("reply_count", 0),
                    quote_count=m.get("quote_count", 0),
                )
            )
        return tweets


def engagement_per_min(tweets: list[XTweet], now: datetime) -> float:
    if not tweets:
        return 0.0
    total = sum(t.like_count + t.repost_count + t.reply_count + t.quote_count for t in tweets)
    oldest = min(t.created_at for t in tweets)
    minutes = (now - oldest).total_seconds() / 60
    return total / max(1.0, minutes)


def controversy(tweets: list[XTweet]) -> float:
    likes = sum(t.like_count for t in tweets)
    return sum(t.reply_count + t.quote_count for t in tweets) / max(1, likes)
