"""Sources of free Udemy courses.

Three providers are included:

* TutorialBarProvider    - reads the structured course data that tutorialbar.com
  embeds in its pages. Needs no credentials and is by far the highest yield, so
  it is the source that carries the bot by default.
* UdemyAffiliateProvider - the official Udemy Affiliate API. Returns courses
  that are permanently free. Requires an approved affiliate account; without
  credentials the API answers 403, so this stays dormant until you have them.
* RSSCouponProvider      - generic RSS/Atom reader for coupon aggregator feeds.
  Useful breadth, but most aggregators either block non-browser traffic or only
  link to their own landing pages, so treat it as a supplement.

All return the same Course object, so the bot doesn't care where a course came
from, and every provider keys its ids on the Udemy course slug so the same
course found by two sources is only announced once. Add your own by subclassing
Provider.
"""

from __future__ import annotations

import asyncio
import base64
import html as html_mod
import json
import logging
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import httpx

from bot.http_utils import fetch_with_retry

log = logging.getLogger(__name__)

UDEMY_API = "https://www.udemy.com/api-2.0/courses/"
UDEMY_BASE = "https://www.udemy.com"

# Several aggregators (and Reddit) reject or throttle the default httpx
# User-Agent outright, which showed up as an empty feed rather than an error.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

#: Internal provider id -> the platform name shown to users in search results.
PROVIDER_LABELS = {
    "tutorialbar": "Udemy",      # tutorialbar only ever links to udemy.com
    "udemy-api": "Udemy",
    "rss-coupons": "Udemy",      # ditto - the feeds are udemy coupon feeds
    "coursera": "Coursera",
    "edx": "edX",
    "mit": "MIT OpenCourseWare",
    "freecodecamp": "freeCodeCamp",
}


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

    # Added for multi-platform search. Every one is optional and defaults to
    # empty, so the coupon providers, the SQLite store and the broadcast
    # formatter all keep working untouched; a platform that does not expose a
    # given field simply leaves it blank rather than failing the result.
    price: str = ""              # "Free", "Free to audit", "Paid", ...
    duration: str = ""           # free-text, platforms never agree on units
    level: str = ""              # "Beginner", "Introductory", ...
    image: str = ""

    @property
    def provider(self) -> str:
        """Human-facing name of the platform this course came from.

        `source` is the internal provider id and is what the store already
        persists; this is just the display alias the search UI asks for.
        """
        return PROVIDER_LABELS.get(self.source, self.source)

    def search_blob(self) -> str:
        """Lowercased text used for keyword filtering."""
        return " ".join(
            [self.title, self.headline, self.instructor, self.level, *self.tags]
        ).lower()


def course_slug(url: str) -> str:
    """The udemy course slug, which is the stable identity of a course.

    Coupon codes rotate constantly, so the slug - not the full URL - is what
    we dedupe on, and it lets two providers that find the same course agree
    on one id instead of announcing it twice.
    """
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()


def filter_courses(courses: list[Course], query: str, limit: int = 10) -> list[Course]:
    """Keep the courses matching every whitespace-separated term in `query`.

    Reuses Course.search_blob(), the same text the /filter subscription
    keywords already match against, so search and filtering agree on what a
    course "contains".
    """
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return courses[:limit]

    # A course whose *title* carries the query is a better answer than one that
    # merely mentions it in the description - otherwise a "Rust" course whose
    # blurb name-drops Python outranks actual Python courses.
    scored = []
    for course in courses:
        if not all(t in course.search_blob() for t in terms):
            continue
        title = course.title.lower()
        scored.append((sum(1 for t in terms if t in title), course))

    # Stable sort, so within a relevance tier the provider's own order wins.
    scored.sort(key=lambda pair: -pair[0])
    return [course for _, course in scored[:limit]]


class Provider:
    name = "provider"

    async def fetch(self) -> list[Course]:  # pragma: no cover - interface
        raise NotImplementedError

    #: How long the search path may reuse a fetch() result, in seconds.
    #: Only /search reads this cache - poll_job always calls fetch() directly
    #: and so always sees fresh data.
    SEARCH_CACHE_TTL = 600

    _cache: tuple[float, list[Course]] | None = None

    async def search(self, query: str, limit: int = 10) -> list[Course]:
        """Find courses matching `query`.

        The default implementation pulls what the provider already offers via
        fetch() and filters locally. That is exactly right for the coupon
        providers, which have no search endpoint - so they gain /search
        without being modified at all. Providers backed by a real search API
        override this.

        The fetch is cached briefly because these providers page over a whole
        site: without it, every /search would re-crawl fourteen pages and take
        twenty seconds. The cache is per-provider-instance and short-lived, so
        results stay current while back-to-back searches are quick.
        """
        now = time.monotonic()
        if self._cache and now - self._cache[0] < self.SEARCH_CACHE_TTL:
            courses = self._cache[1]
        else:
            courses = await self.fetch()
            self._cache = (now, courses)
        return filter_courses(courses, query, limit)

    async def _get(self, client: httpx.AsyncClient, url: str):
        """GET a URL, retrying once when the server asks us to back off."""
        return await fetch_with_retry(client, url, logger=log, name=self.name)


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
        url = UDEMY_BASE + path if path.startswith("/") else path
        # Key on the slug like the other providers so the same course found by
        # two sources is announced once, falling back to the numeric id.
        slug = course_slug(url) or str(item.get("id", ""))
        return Course(
            id=f"udemy:{slug}",
            title=item.get("title", "Untitled"),
            url=url,
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
    # Anchor on /course/ so we match real course pages and not the udemy CDN
    # image URLs (udemy-images.udemy.com, img-c.udemycdn.com) that most feeds
    # put in their <enclosure> tags.
    UDEMY_LINK_RE = re.compile(
        r"https?://(?:www\.)?udemy\.com/course/[^\s\"'<>]+", re.I
    )

    # Politeness gap between feeds. Requesting several Reddit feeds back to
    # back reliably earns a 429 for everything after the first one.
    FEED_DELAY = 2.0

    def __init__(self, feed_urls: Iterable[str], timeout: float = 30.0) -> None:
        self.feed_urls = [u for u in feed_urls if u]
        self.timeout = timeout

    async def fetch(self) -> list[Course]:
        if not self.feed_urls:
            return []

        import feedparser  # imported lazily so the API-only setup stays light

        headers = {
            "User-Agent": BROWSER_UA,
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        }

        courses: list[Course] = []
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True, headers=headers
        ) as client:
            for index, url in enumerate(self.feed_urls):
                if index:
                    await asyncio.sleep(self.FEED_DELAY)

                resp = await self._get(client, url)
                if resp is None:
                    continue

                parsed = feedparser.parse(resp.content)
                if not parsed.entries:
                    log.warning(
                        "Feed %s returned no entries (content-type=%s). It may "
                        "be an error page rather than a feed.",
                        url,
                        resp.headers.get("content-type", "?"),
                    )
                    continue

                found = 0
                for entry in parsed.entries:
                    course = self._parse(entry)
                    if course:
                        courses.append(course)
                        found += 1

                # Without this, a feed that parses fine but carries no Udemy
                # links is indistinguishable from one that was never fetched.
                log.info(
                    "Feed %s: %d entries, %d with a Udemy course link",
                    url,
                    len(parsed.entries),
                    found,
                )
                if not found:
                    log.warning(
                        "Feed %s has entries but none link to udemy.com/course/"
                        " - it probably links to its own landing pages instead.",
                        url,
                    )

        log.info("%s: %d courses", self.name, len(courses))
        return courses

    def _parse(self, entry) -> Course | None:
        match = self.UDEMY_LINK_RE.search(self._blob(entry))
        if not match:
            return None

        link = match.group(0).rstrip(".,)")
        coupon = self.COUPON_RE.search(link)

        return Course(
            id=f"udemy:{course_slug(link)}",
            title=entry.get("title", "Untitled").strip(),
            url=link,
            source=self.name,
            headline=(entry.get("summary", "") or "")[:200],
            coupon_code=coupon.group(1) if coupon else None,
            tags=[t.get("term") or "" for t in entry.get("tags", []) or []],
        )

    @staticmethod
    def _blob(entry) -> str:
        """Every field an aggregator might hide the real course link in.

        Feeds vary a lot: some put the Udemy URL in the entry link, some only
        in the HTML body, some in a second <link>. Entities are unescaped so
        that an href written as `...?couponCode=X&amp;utm=y` still yields the
        coupon code.
        """
        parts = [
            entry.get("link", ""),
            entry.get("id", ""),
            entry.get("summary", ""),
        ]
        for link in entry.get("links", []) or []:
            parts.append(link.get("href", ""))
        for content in entry.get("content", []) or []:
            parts.append(content.get("value", ""))
        return html_mod.unescape(" ".join(p for p in parts if p))


class TutorialBarProvider(Provider):
    """Highest-yield source: the embedded course data on tutorialbar.com.

    The site is a React Server Components app, and every page ships the course
    records it rendered as a JSON payload split across ``self.__next_f.push``
    chunks. Reading that payload is both far more reliable and far cheaper than
    scraping the rendered HTML: one request yields a dozen fully-populated
    courses (title, instructor, rating, coupon URL, and an ``isExpired`` flag)
    instead of a page of anchors we would have to guess our way through.

    Each category page returns a different slice of the catalogue, so fetching
    the front page plus the categories is what takes a poll from ~12 courses to
    ~90.
    """

    name = "tutorialbar"
    BASE = "https://tutorialbar.com"

    #: The site's own category slugs. Anything unknown simply yields nothing,
    #: so an out-of-date entry here degrades yield rather than breaking a poll.
    CATEGORIES = (
        "development",
        "business",
        "finance-and-accounting",
        "it-and-software",
        "office-productivity",
        "personal-development",
        "design",
        "marketing",
        "lifestyle",
        "photography-and-video",
        "health-and-fitness",
        "music",
        "teaching-and-academics",
    )

    # RSC flight chunks. Each is a JSON string literal, so json.loads gives us
    # correct unescaping instead of a pile of hand-rolled .replace() calls.
    _PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*(".*?")\]\)', re.S)
    _OBJECT_START = '{"id":"'

    def __init__(
        self,
        categories: Iterable[str] | None = None,
        concurrency: int = 4,
        timeout: float = 30.0,
    ) -> None:
        cats = tuple(categories) if categories is not None else self.CATEGORIES
        self.pages = [f"{self.BASE}/"] + [
            f"{self.BASE}/category/{c.strip()}" for c in cats if c.strip()
        ]
        self.concurrency = max(1, concurrency)
        self.timeout = timeout

    async def fetch(self) -> list[Course]:
        headers = {"User-Agent": BROWSER_UA, "Accept": "text/html,*/*"}
        # Keyed by slug so the overlap between category pages collapses.
        found: dict[str, Course] = {}
        expired = 0
        failed = 0

        sem = asyncio.Semaphore(self.concurrency)

        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True, headers=headers
        ) as client:

            async def scrape(url: str) -> tuple[list[dict], bool]:
                async with sem:
                    resp = await self._get(client, url)
                if resp is None:
                    return [], False
                return list(self._course_objects(self._rsc_payload(resp.text))), True

            results = await asyncio.gather(
                *(scrape(u) for u in self.pages), return_exceptions=True
            )

        for url, result in zip(self.pages, results):
            if isinstance(result, Exception):
                log.error("%s: %s raised: %s", self.name, url, result)
                failed += 1
                continue
            records, ok = result
            if not ok:
                failed += 1
                continue
            for record in records:
                if record.get("isExpired"):
                    expired += 1
                    continue
                course = self._to_course(record)
                if course:
                    found.setdefault(course.id, course)

        if failed == len(self.pages):
            log.error(
                "%s: every page failed - the site may be down or has changed.",
                self.name,
            )
        elif not found:
            log.warning(
                "%s: pages fetched but no course records parsed. The site's "
                "payload format has probably changed.",
                self.name,
            )

        log.info(
            "%s: %d courses from %d/%d pages (%d expired skipped)",
            self.name,
            len(found),
            len(self.pages) - failed,
            len(self.pages),
            expired,
        )
        return list(found.values())

    @classmethod
    def _rsc_payload(cls, html: str) -> str:
        """Reassemble the RSC flight chunks into one searchable string."""
        parts = []
        for match in cls._PUSH_RE.finditer(html):
            try:
                parts.append(json.loads(match.group(1)))
            except json.JSONDecodeError:
                continue  # a chunk we can't read shouldn't lose us the rest
        return "".join(parts)

    @classmethod
    def _course_objects(cls, payload: str) -> Iterator[dict]:
        """Yield each course record embedded in the payload.

        The records sit inline in a much larger structure, so rather than
        parsing the whole payload we find each object that carries a coupon
        URL and brace-match just that object.
        """
        index = 0
        while True:
            marker = payload.find('"couponUrl"', index)
            if marker == -1:
                return
            start = payload.rfind(cls._OBJECT_START, 0, marker)
            if start == -1:
                index = marker + 1
                continue

            end = cls._match_object(payload, start)
            if end is None:
                index = marker + 1
                continue

            try:
                record = json.loads(payload[start : end + 1])
            except json.JSONDecodeError:
                index = marker + 1
                continue

            if isinstance(record, dict) and record.get("couponUrl"):
                yield record
            index = max(end, marker + 1)

    @staticmethod
    def _match_object(text: str, start: int) -> int | None:
        """Index of the brace closing the object that opens at ``start``."""
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            char = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return i
        return None

    def _to_course(self, record: dict) -> Course | None:
        url = (record.get("couponUrl") or "").strip()
        slug = record.get("slug") or course_slug(url)
        if not url or not slug:
            return None

        category = record.get("category")
        return Course(
            id=f"udemy:{slug.lower()}",
            title=(record.get("title") or "Untitled").strip(),
            url=url,
            source=self.name,
            headline=(record.get("headline") or "").strip()[:200],
            instructor=(record.get("instructorName") or "").strip(),
            rating=self._number(record.get("rating"), float),
            students=self._number(record.get("studentsCount"), int),
            coupon_code=record.get("couponCode") or None,
            tags=[category] if category else [],
        )

    @staticmethod
    def _number(value, cast):
        try:
            return cast(value)
        except (TypeError, ValueError):
            return None
