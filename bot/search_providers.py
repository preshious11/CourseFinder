"""Course providers for platforms other than Udemy, plus the search fan-out.

These are search-first providers: unlike the coupon providers in providers.py,
which poll a firehose of new deals, these answer "find me courses about X".
They subclass the same Provider base and return the same Course object, so the
existing store, filters and formatters work with them unchanged.

Only platforms with an official or openly published endpoint are implemented.
What each one actually supports was verified against the live APIs rather than
assumed, because the answers are not what the marketing pages suggest:

* MIT (learn.mit.edu)  - a real relevance-ranked search API. The best of the
  four; MIT Open Learning covers OCW and MITx.
* Coursera             - the public catalogue is readable and pages via
  start/limit, but its documented `q=search` finder answers
  "finder 'search' not implemented", so matching is done locally over a
  bounded slice of the catalogue.
* edX (courses.edx.org)- the public course list is readable, but `search_term`
  is a no-op: it returns an identical result count for a real query, a
  nonsense query and no query at all. So, again, local matching.
* freeCodeCamp         - no course API; the published news/tutorial RSS feed is
  the supported public surface, and it is small (one page of recent posts).

Deliberately not implemented, because there is no permitted way in:
  Class Central  - /api/search 404s; no public API.
  Khan Academy   - the old open API answers 410 Gone.
  FutureLearn    - no public API.
  Pluralsight    - partner API, credentials are not self-serve.
  LinkedIn Learning - partner API, credentials are not self-serve.
None of these are scraped or worked around; they are simply absent.
"""

from __future__ import annotations

import asyncio
import html as html_mod
import logging
import re
import time

import httpx

from bot.providers import BROWSER_UA, Course, Provider, filter_courses

log = logging.getLogger(__name__)

JSON_HEADERS = {"User-Agent": BROWSER_UA, "Accept": "application/json"}

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str | None, limit: int = 300) -> str:
    """Strip HTML and collapse whitespace - feed and API prose is messy."""
    if not text:
        return ""
    plain = html_mod.unescape(_TAG_RE.sub(" ", str(text)))
    return re.sub(r"\s+", " ", plain).strip()[:limit]


def _names(value) -> str:
    """Flatten MIT-style vocabulary fields into text.

    Several MIT fields (pace, format, ...) come back as [{"code": .., "name": ..}]
    rather than plain strings, so joining them naively raises TypeError.
    """
    if not value:
        return ""
    if isinstance(value, str):
        return value
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append(str(item.get("name") or item.get("code") or ""))
        else:
            out.append(str(item))
    return ", ".join(x for x in out if x)


class SearchProvider(Provider):
    """A provider whose natural operation is search, not polling.

    fetch() is what the hourly poll job calls. These platforms are catalogues
    rather than streams of new free coupons, so announcing "new" courses from
    them would be noise; fetch() returns nothing and they take part in /search
    only. That keeps them out of the broadcast loop by construction.
    """

    timeout = 20.0

    async def fetch(self) -> list[Course]:
        return []

    async def _get_json(self, client: httpx.AsyncClient, url: str, params: dict):
        """GET returning parsed JSON, or None. Retries once on 429/503."""
        for attempt in (1, 2):
            try:
                resp = await client.get(url, params=params, headers=JSON_HEADERS)
            except httpx.HTTPError as exc:
                # Some httpx errors (timeouts especially) stringify to "", so
                # name the class or the log line says nothing at all.
                log.error("%s: request failed: %s: %s",
                          self.name, type(exc).__name__, exc or "(no detail)")
                return None

            if resp.status_code in self.RETRY_STATUS and attempt == 1:
                wait = self._retry_after(resp)
                log.warning("%s: HTTP %s, retrying in %.0fs",
                            self.name, resp.status_code, wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code != 200:
                log.error("%s: HTTP %s from %s", self.name, resp.status_code, url)
                return None

            try:
                return resp.json()
            except ValueError:
                log.error("%s: response was not JSON", self.name)
                return None
        return None


class PagedCatalogueProvider(SearchProvider):
    """Base for platforms that publish a catalogue but no working search.

    Coursera and edX both fall in this bucket, so both crawl a fixed window of
    catalogue pages and match locally. Because that window is identical for
    every query, the crawl is cached: the first /search pays for it and later
    ones filter the cached catalogue in memory. That turns a ~20s search into a
    near-instant one and is markedly politer to their APIs than re-crawling
    five pages every time somebody types a word.
    """

    CACHE_TTL = 600

    def __init__(self, pages: int = 5, timeout: float = 20.0) -> None:
        self.pages = max(1, pages)
        self.timeout = timeout
        self._catalogue: tuple[float, list[Course]] | None = None

    async def _crawl(self) -> list[Course]:  # pragma: no cover - interface
        raise NotImplementedError

    async def _load_catalogue(self) -> list[Course]:
        now = time.monotonic()
        if self._catalogue and now - self._catalogue[0] < self.CACHE_TTL:
            return self._catalogue[1]
        courses = await self._crawl()
        # Keep a stale catalogue rather than caching a failure, so one bad
        # crawl doesn't blank this platform for the whole TTL.
        if courses or self._catalogue is None:
            self._catalogue = (now, courses)
        return self._catalogue[1]

    async def search(self, query: str, limit: int = 10) -> list[Course]:
        courses = await self._load_catalogue()
        hits = filter_courses(courses, query, limit)
        log.info(
            "%s: %d results for %r (scanned %d catalogue entries)",
            self.name, len(hits), query, len(courses),
        )
        return hits


class MITProvider(SearchProvider):
    """MIT Open Learning - OCW and MITx.

    The only one of these platforms offering genuine server-side relevance
    search, so the query goes straight to the API.
    """

    name = "mit"
    API = "https://api.learn.mit.edu/api/v1/learning_resources_search/"

    async def search(self, query: str, limit: int = 10) -> list[Course]:
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True
        ) as client:
            data = await self._get_json(
                client,
                self.API,
                {"q": query, "limit": limit, "resource_category": "course"},
            )
        if not data:
            return []

        courses = []
        for item in data.get("results", []) or []:
            course = self._to_course(item)
            if course:
                courses.append(course)
        log.info("%s: %d results for %r", self.name, len(courses), query)
        return courses[:limit]

    def _to_course(self, item: dict) -> Course | None:
        url = item.get("url") or ""
        title = (item.get("title") or "").strip()
        if not url or not title:
            return None

        platform = (item.get("platform") or {}).get("name") or ""
        offered = (item.get("offered_by") or {}).get("name") or ""
        image = (item.get("image") or {}).get("url") or ""

        return Course(
            id=f"mit:{item.get('readable_id') or item.get('id')}",
            title=title,
            url=url,
            source=self.name,
            headline=_clean(item.get("description")),
            instructor=offered,
            price="Free" if item.get("free") else "Paid",
            level=_clean(_names(item.get("pace")), 40),
            duration=_clean(_names(item.get("time_commitment")), 40),
            image=image,
            tags=[platform] if platform else [],
        )


class CourseraProvider(PagedCatalogueProvider):
    """Coursera's public catalogue.

    Its `q=search` finder is not implemented server-side, so we page through a
    bounded slice of the catalogue and match locally. `pages` caps the work: at
    100 per page the default is 500 courses per query, which keeps a /search
    responsive rather than exhaustive.
    """

    name = "coursera"
    API = "https://api.coursera.org/api/courses.v1"
    FIELDS = "name,slug,description,photoUrl,workload,primaryLanguages"
    PAGE_SIZE = 100

    async def _crawl(self) -> list[Course]:
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True
        ) as client:
            pages = await asyncio.gather(
                *(
                    self._get_json(
                        client,
                        self.API,
                        {
                            "start": page * self.PAGE_SIZE,
                            "limit": self.PAGE_SIZE,
                            "fields": self.FIELDS,
                        },
                    )
                    for page in range(self.pages)
                ),
                return_exceptions=True,
            )

        courses = []
        for page in pages:
            if isinstance(page, Exception) or not page:
                continue
            for item in page.get("elements", []) or []:
                course = self._to_course(item)
                if course:
                    courses.append(course)
        return courses

    def _to_course(self, item: dict) -> Course | None:
        slug = item.get("slug")
        name = (item.get("name") or "").strip()
        if not slug or not name:
            return None
        return Course(
            id=f"coursera:{slug}",
            title=name,
            url=f"https://www.coursera.org/learn/{slug}",
            source=self.name,
            headline=_clean(item.get("description")),
            duration=_clean(item.get("workload"), 40),
            price="Free to audit",
            image=item.get("photoUrl") or "",
        )


class EdXProvider(PagedCatalogueProvider):
    """edX's public course list on courses.edx.org.

    `search_term` is accepted but ignored by the endpoint (verified: identical
    result counts for a real query, a nonsense query and no query), so matching
    is local over a bounded page window. That means recall is partial - this
    provider finds what is in the pages it scans, not everything edX has. The
    authenticated Discovery API would be needed for true search, and it answers
    401 without partner credentials.
    """

    name = "edx"
    API = "https://courses.edx.org/api/courses/v1/courses/"
    PAGE_SIZE = 100

    async def _crawl(self) -> list[Course]:
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True
        ) as client:
            pages = await asyncio.gather(
                *(
                    self._get_json(
                        client,
                        self.API,
                        {"page": page, "page_size": self.PAGE_SIZE},
                    )
                    for page in range(1, self.pages + 1)
                ),
                return_exceptions=True,
            )

        courses = []
        for page in pages:
            if isinstance(page, Exception) or not page:
                continue
            for item in page.get("results", []) or []:
                course = self._to_course(item)
                if course:
                    courses.append(course)
        return courses

    def _to_course(self, item: dict) -> Course | None:
        course_id = item.get("id")
        name = (item.get("name") or "").strip()
        if not course_id or not name:
            return None

        media = item.get("media") or {}
        image = ""
        for key in ("course_image", "image", "banner_image"):
            block = media.get(key) or {}
            if isinstance(block, dict):
                image = block.get("uri") or block.get("raw") or ""
            if image:
                break
        if image.startswith("/"):
            image = "https://courses.edx.org" + image

        return Course(
            id=f"edx:{course_id}",
            title=name,
            url=item.get("blocks_url", "").split("/api/")[0]
                + f"/courses/{course_id}/about"
                if item.get("blocks_url")
                else f"https://courses.edx.org/courses/{course_id}/about",
            source=self.name,
            headline=_clean(item.get("short_description")),
            instructor=_clean(item.get("org"), 60),
            duration=_clean(item.get("effort"), 40),
            price="Free to audit",
            image=image,
        )


class FreeCodeCampProvider(SearchProvider):
    """freeCodeCamp's published tutorial/news RSS feed.

    freeCodeCamp has no course API. The RSS feed is the supported public
    surface and carries only the most recent posts, so this contributes a
    small number of highly recent tutorials rather than a catalogue.
    """

    name = "freecodecamp"
    FEED = "https://www.freecodecamp.org/news/rss/"

    async def search(self, query: str, limit: int = 10) -> list[Course]:
        import feedparser  # lazy, matching RSSCouponProvider's approach

        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True,
            headers={"User-Agent": BROWSER_UA},
        ) as client:
            resp = await self._get(client, self.FEED)
        if resp is None:
            return []

        courses = []
        for entry in feedparser.parse(resp.content).entries:
            link = entry.get("link") or ""
            title = (entry.get("title") or "").strip()
            if not link or not title:
                continue
            courses.append(
                Course(
                    id=f"fcc:{link.rstrip('/').rsplit('/', 1)[-1]}",
                    title=title,
                    url=link,
                    source=self.name,
                    headline=_clean(entry.get("summary")),
                    instructor=_clean(entry.get("author"), 60),
                    price="Free",
                    tags=[t.get("term", "") for t in entry.get("tags", []) or []],
                )
            )

        hits = filter_courses(courses, query, limit)
        log.info("%s: %d results for %r (of %d recent posts)",
                 self.name, len(hits), query, len(courses))
        return hits


def _dedupe_key(course: Course) -> str:
    """Identity used to collapse the same course arriving from two platforms."""
    return re.sub(r"[^a-z0-9]+", " ", course.title.lower()).strip()


async def search_all_providers(
    providers: list[Provider],
    query: str,
    limit_per_provider: int = 10,
    total_limit: int = 30,
    timeout: float = 25.0,
) -> list[Course]:
    """Search every provider concurrently and merge the results.

    A provider that fails, times out or returns nothing is logged and skipped;
    the others' results are still returned. Results are interleaved so that one
    fast, high-volume platform cannot crowd the others out of the top of the
    list, then de-duplicated by title, keeping the first occurrence so the
    provider order passed in acts as the canonical-source preference.
    """
    if not providers:
        return []

    async def run(provider: Provider) -> list[Course]:
        try:
            return await asyncio.wait_for(
                provider.search(query, limit_per_provider), timeout=timeout
            )
        except TimeoutError:
            log.warning("%s: search timed out after %.0fs", provider.name, timeout)
            return []
        except Exception:  # one bad provider must not sink the search
            # exception() keeps the traceback: swallowing these silently once
            # hid a real bug behind an innocent-looking "0 results".
            log.exception("%s: search failed", provider.name)
            return []

    results = await asyncio.gather(*(run(p) for p in providers))

    for provider, hits in zip(providers, results):
        log.info("search %r: %s -> %d", query, provider.name, len(hits))

    # Round-robin interleave so every platform is represented near the top.
    merged: list[Course] = []
    for position in range(max((len(r) for r in results), default=0)):
        for hits in results:
            if position < len(hits):
                merged.append(hits[position])

    seen: set[str] = set()
    deduped: list[Course] = []
    for course in merged:
        key = _dedupe_key(course)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(course)
        if len(deduped) >= total_limit:
            break
    return deduped
