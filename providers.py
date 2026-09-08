"""Sources of free Udemy courses.

Two providers are included:

* UdemyAffiliateProvider - the official Udemy Affiliate API. Returns courses
  that are permanently free. Requires an approved affiliate account.
* RSSCouponProvider      - generic RSS/Atom reader for coupon aggregator feeds.
  This is where "100% off for the next 3 days" deals come from.

Both return the same Course object, so the bot doesn't care where a course
came from. Add your own provider by subclassing Provider.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

import httpx

log = logging.getLogger(__name__)

UDEMY_API = "https://www.udemy.com/api-2.0/courses/"
UDEMY_BASE = "https://www.udemy.com"


@dataclass
class Course:
    """One course, normalised across every provider."""

    id: str                      # stable unique key, used for dedupe
    title: str
    url: str
    source: str                  # which provider found it
    headline: str = ""
    instructor: str = ""
    rating: float | None = None
    students: int | None = None
    coupon_code: str | None = None
    expires: str | None = None   # free-text, coupon feeds rarely agree on format
    tags: list[str] = field(default_factory=list)

    def search_blob(self) -> str:
        """Lowercased text used for keyword filtering."""
        return " ".join(
            [self.title, self.headline, self.instructor, *self.tags]
        ).lower()


class Provider:
    name = "provider"

    async def fetch(self) -> list[Course]:  # pragma: no cover - interface
        raise NotImplementedError


class UdemyAffiliateProvider(Provider):
    """Official Udemy Affiliate API.

    Credentials come from https://www.udemy.com/user/edit-api-clients/ once
    your affiliate application is approved. Auth is HTTP Basic with
    client_id:client_secret.
    """

    name = "udemy-api"

    FIELDS = (
        "@min,headline,url,visible_instructors,avg_rating_recent,"
        "num_subscribers,image_480x270"
    )

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        language: str = "en",
        pages: int = 2,
        page_size: int = 100,
        category: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        token = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json, text/plain, */*",
        }
        self.language = language
        self.pages = pages
        self.page_size = page_size
        self.category = category
        self.timeout = timeout

    async def fetch(self) -> list[Course]:
        courses: list[Course] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for page in range(1, self.pages + 1):
                params = {
                    "page": page,
                    "page_size": self.page_size,
                    "price": "price-free",
                    "ordering": "newest",
                    "language": self.language,
                    "fields[course]": self.FIELDS,
                }
                if self.category:
                    params["category"] = self.category

                try:
                    resp = await client.get(
                        UDEMY_API, params=params, headers=self._headers
                    )
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    log.error(
                        "Udemy API returned %s: %s",
                        exc.response.status_code,
                        exc.response.text[:200],
                    )
                    break
                except httpx.HTTPError as exc:
                    log.error("Udemy API request failed: %s", exc)
                    break

                payload = resp.json()
                results = payload.get("results", [])
                courses.extend(self._parse(item) for item in results)

                if not payload.get("next"):
                    break

        log.info("%s: %d courses", self.name, len(courses))
        return courses

    def _parse(self, item: dict) -> Course:
        instructors = item.get("visible_instructors") or []
        instructor = instructors[0].get("title", "") if instructors else ""
        path = item.get("url", "")
        return Course(
            id=f"udemy:{item['id']}",
            title=item.get("title", "Untitled"),
            url=UDEMY_BASE + path if path.startswith("/") else path,
            source=self.name,
            headline=item.get("headline") or "",
            instructor=instructor,
            rating=item.get("avg_rating_recent"),
            students=item.get("num_subscribers"),
        )


class RSSCouponProvider(Provider):
    """Reads coupon deals from one or more RSS/Atom feeds.

    Aggregators publish feeds in wildly different shapes, so this keeps
    parsing deliberately loose: take the title, take the first udemy.com link
    it can find, pull the couponCode out of the query string if present.

    Check each feed's terms before using it, and be a good citizen about
    polling frequency.
    """

    name = "rss-coupons"

    COUPON_RE = re.compile(r"couponCode=([A-Za-z0-9_\-\.]+)", re.I)
    UDEMY_LINK_RE = re.compile(r"https?://(?:www\.)?udemy\.com/[^\s\"'<>]+", re.I)

    def __init__(self, feed_urls: Iterable[str], timeout: float = 30.0) -> None:
        self.feed_urls = [u for u in feed_urls if u]
        self.timeout = timeout

    async def fetch(self) -> list[Course]:
        if not self.feed_urls:
            return []

        import feedparser  # imported lazily so the API-only setup stays light

        courses: list[Course] = []
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True
        ) as client:
            for url in self.feed_urls:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    log.error("Feed %s failed: %s", url, exc)
                    continue

                parsed = feedparser.parse(resp.text)
                for entry in parsed.entries:
                    course = self._parse(entry)
                    if course:
                        courses.append(course)

        log.info("%s: %d courses", self.name, len(courses))
        return courses

    def _parse(self, entry) -> Course | None:
        blob = " ".join(
            filter(
                None,
                [
                    entry.get("link", ""),
                    entry.get("summary", ""),
                    getattr(entry, "content", [{}])[0].get("value", "")
                    if getattr(entry, "content", None)
                    else "",
                ],
            )
        )
        match = self.UDEMY_LINK_RE.search(blob)
        if not match:
            return None

        link = match.group(0).rstrip(".,)")
        coupon = self.COUPON_RE.search(link)
        # Dedupe on the course slug, not the full URL - coupon codes rotate
        # and we don't want to re-announce the same course every rotation.
        slug = link.split("?")[0].rstrip("/").split("/")[-1]

        return Course(
            id=f"coupon:{slug}",
            title=entry.get("title", "Untitled").strip(),
            url=link,
            source=self.name,
            headline=(entry.get("summary", "") or "")[:200],
            coupon_code=coupon.group(1) if coupon else None,
            expires=entry.get("published"),
            tags=[t.get("term", "") for t in entry.get("tags", [])],
        )
