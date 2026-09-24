"""Shared HTTP helpers used by both coupon and search providers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = (429, 503)


def retry_after(resp: httpx.Response, default: float = 10.0) -> float:
    """Parse the Retry-After header, clamped to a sane maximum."""
    try:
        return min(float(resp.headers.get("retry-after", default)), 60.0)
    except (TypeError, ValueError):
        return default


async def fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    logger: logging.Logger | None = None,
    name: str = "http",
) -> httpx.Response | None:
    """GET *url*, retrying once when the server asks us to back off.

    Returns the response on success, or ``None`` on failure / non-200.
    """
    log_ = logger or log
    for attempt in (1, 2):
        try:
            resp = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            log_.error("%s: %s failed: %s: %s", name, url, type(exc).__name__, exc or "(no detail)")
            return None

        if resp.status_code in RETRY_STATUS and attempt == 1:
            wait = retry_after(resp)
            log_.warning("%s: %s returned %s, retrying in %.0fs", name, url, resp.status_code, wait)
            await asyncio.sleep(wait)
            continue

        if resp.status_code != 200:
            log_.error("%s: %s returned HTTP %s", name, url, resp.status_code)
            return None

        return resp
    return None


async def fetch_json_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    logger: logging.Logger | None = None,
    name: str = "http",
) -> Any | None:
    """GET *url* and parse JSON, retrying once on 429/503.

    Returns the parsed JSON on success, or ``None`` on failure.
    """
    resp = await fetch_with_retry(client, url, params=params, headers=headers, logger=logger, name=name)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        (logger or log).error("%s: response from %s was not JSON", name, url)
        return None
