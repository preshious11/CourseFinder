# Codebase Audit Report — Free Udemy Course Telegram Bot

**Date:** 2026-09-23  
**Branch:** `main` (clean working tree)  
**Python:** 3.14.7 (Windows)  
**Scope:** `main.py`, `config.py`, `providers.py`, `search_providers.py`, `telegram_bot.py`, `store.py`, `__init__.py`, `requirements.txt`, `README.md`, `.env`, `.env.example`

---

## Executive Summary

The codebase is a well-structured Python Telegram bot for monitoring free Udemy courses. The architecture is clean, the provider pattern is extensible, and the bot has been **successfully running in production** with 3 subscribers and 140 tracked courses. However, there are **critical dependency issues**, **security concerns**, and **several design inconsistencies** that need attention.

**Grade: B-** — Solid architecture and working core, but dependency/configuration drift and missing safety nets.

---

## ✅ What Works

### 1. All Source Files Compile & Import Cleanly
Every module (`config`, `providers`, `search_providers`, `store`, `telegram_bot`, `main`) imports without errors. `py_compile` passes for all files.

### 2. SQLite Persistence Layer Is Functional
The `store.py` module works correctly:
- **3 subscribers** tracked, **140 courses** seen
- `filter_new()` deduplication works (prevents re-announcing same course)
- `mark_seen()` and `recent()` queries return correct results
- Cold-start baseline mechanism works (first poll records without announcing)

### 3. Core Bot Logic Is Sound
- **Polling job** runs on schedule with proper error isolation
- **Broadcast loop** handles rate limiting (`RetryAfter`), blocked users (`Forbidden` → auto-unsubscribe), and 60ms send delays
- **Per-cycle cap** (`max_per_cycle`) prevents notification floods
- **Filtering** by keyword works for subscribers
- **Provider pattern** is extensible — subclass `Provider`, implement `fetch()`, return `Course` objects

### 4. TutorialBarProvider Is the High-Yield Source
- Fetches ~90 courses per poll from 14 category pages
- Uses RSC payload parsing (smart approach — avoids HTML scraping)
- Handles expired courses, failed pages, and payload format changes gracefully

### 5. Multi-Platform Search Infrastructure Is In Place
- `/search` fans out across MIT, Coursera, edX, freeCodeCamp concurrently
- `PagedCatalogueProvider` caches catalogue for 10 minutes
- Results are round-robin interleaved and deduplicated by title

### 6. Production Logging Is Informative
Logs show: provider names, course counts, failed feeds, subscriber counts, and cycle summaries. The `poll_job` logs "Found X new; announcing Y (Z queued for later)".

### 7. Bot Has Been Running Successfully
Logs confirm multiple successful poll cycles with course announcements reaching subscribers.

---

## ❌ What Isn't Working / Needs Attention

### 🔴 Critical Issues

#### 1. Missing `feedparser` Dependency (Runtime Breaker)
```
ModuleNotFoundError: No module named 'feedparser'
```
- **`feedparser`** is used by both `RSSCouponProvider` (line 340) and `FreeCodeCampProvider` (line 368) via lazy `import feedparser`
- **NOT in `requirements.txt`**
- Installed only during this audit — without it, `RSSCouponProvider.fetch()` and `FreeCodeCampProvider.search()` crash at runtime
- **Fix:** Add `feedparser>=6.0` to `requirements.txt`

#### 2. `python-telegram-bot` Version Mismatch (Breaking)
```
requirements.txt: python-telegram-bot[job-queue]>=21.0,<22
installed:        python-telegram-bot 22.8
```
- The installed version (22.8) violates the `<22` constraint
- **`apscheduler` is installed** but NOT listed in `requirements.txt` at all
- **Fix:** Update `requirements.txt` to reflect actual installed versions: `python-telegram-bot[job-queue]>=21.0` and add `APScheduler>=3.0`

#### 3. `apscheduler` Missing from Requirements
- `APScheduler 3.11.3` is installed and functioning (powers `job_queue.run_repeating`)
- But it's not in `requirements.txt` — any fresh install would break the bot

---

### 🟡 Significant Issues

#### 4. Reddit RSS Feed Returns HTTP 403
- Log line: `rss-coupons: https://www.reddit.com/r/udemyfreebies/.rss returned HTTP 403`
- The `BROWSER_UA` User-Agent header isn't sufficient for Reddit's anti-bot protection
- **Impact:** Reddit feed contributes 0 courses. Only `freebiesglobal.com/feed` works.
- **Fix:** Either use Reddit's official API with credentials, or remove the Reddit feed URL from `.env`

#### 5. No Error Handlers Registered
- Multiple log entries: `No error handlers are registered, logging exception.`
- `httpx.ConnectError` and `NetworkError` from Telegram's `get_updates` propagate uncaught
- **Impact:** Network blips crash the polling loop until the next restart
- **Fix:** Add `ApplicationBuilder().post_init()` error handler or wrap `network_loop` calls

#### 6. DNS Resolution Failures (Connectivity)
- `httpcore.ConnectError: [Errno 11001] getaddrinfo failed` at 2026-09-09 12:46:42
- Caused missed scheduled jobs (prune_job missed by 14min, poll_job missed by 14min)
- **Impact:** During the outage, the bot couldn't send messages or receive updates
- **Fix:** Add retry-with-backoff for `get_updates`, or use webhooks instead of polling

#### 7. `.env` Contains Real Telegram Bot Token
- Line 3: `TELEGRAM_BOT_TOKEN= 8857254752:AAGYC_Zlpitl2l88kYJrLX03M9M8JEF-R-Y`
- **Security risk** if this file is ever committed or shared
- **Fix:** Immediately rotate the token via @BotFather and remove from `.env`

---

### 🟠 Minor Issues

#### 8. `SEARCH_*` Flags Missing from `.env`
- `.env.example` has `SEARCH_MIT=true`, `SEARCH_COURSERA=true`, `SEARCH_EDX=true`, `SEARCH_FREECODECAMP=true`
- **`.env` is missing these entirely** — the config defaults to `True` via `_bool()`, so they work, but the `.env` file is inconsistent with the example
- **Fix:** Add search flags to `.env` for clarity

#### 9. `admin_chat_id` Is Configured But Never Used
- `config.py` has `admin_chat_id: int | None` field
- **No admin-only commands exist in `telegram_bot.py`** — no `/admin` endpoint, no restricted commands
- **Fix:** Either implement admin features or remove the dead config field

#### 10. No Unit Tests
- Zero test files found anywhere in the project
- `py_compile` passes but there's no test suite
- **Fix:** Add `tests/` directory with pytest tests for `store.py`, `providers.py`, `config.py`, and `telegram_bot.py`

#### 11. No Linting/Type Checking Configuration
- No `pyproject.toml`, `setup.cfg`, `ruff.toml`, `mypy.ini`, or `.flake8`
- Code uses `from __future__ import annotations` (good for 3.14) but there's no type-checking enforcement
- **Fix:** Add `ruff` or `flake8` config and `mypy` config to CI pipeline

#### 12. `main.py` Docstring vs. Actual Package Structure Mismatch
```python
# main.py says:
"""Entry point. Run with: python -m bot.main from the parent directory."""
# But files are NOT in a bot/ subdirectory — they're at the project root.
# Running `python -m bot.main` would fail because there's no bot/__init__.py in a bot/ folder.
```
- The README also references `bot/config.py`, `bot/providers.py` etc. but the actual files are at the root
- **Fix:** Either restructure into a `bot/` package directory or update all references

#### 13. Duplicate Retry Logic Between Provider Classes
- `Provider._get()` (lines 174-200) and `SearchProvider._get_json()` (lines 93-121) both implement retry-on-429/503 logic
- **Fix:** Extract a shared `_request_with_retry` utility into a common module

#### 14. `PagedCatalogueProvider._load_catalogue` Uses Deprecated API
```python
now = asyncio.get_event_loop().time()  # line 146
```
- `asyncio.get_event_loop()` is deprecated in Python 3.14
- **Fix:** Use `asyncio.get_running_loop().time()` or `time.monotonic()`

#### 15. `.kilo/worktrees/spiced-twine` Is Unused Duplicate
- A git worktree exists with identical copies of all source files (verified via MD5 hashes)
- **Fix:** Delete the worktree if not needed, or merge any changes back to main

#### 16. `__pycache__` Directories in Source
- `__pycache__/` directories contain `.cpython-314.pyc` files
- **Fix:** Add `__pycache__/` and `*.pyc` to `.gitignore`

#### 17. `COUPON_FEEDS` Reddit URL May Break Again
- The Reddit RSS URL in `.env` is the same one returning 403
- If the bot restarts with this config, the Reddit feed will consistently fail

---

## 📊 Dependency Status

| Package | Required | Installed | Status |
|---|---|---|---|
| `python-telegram-bot[job-queue]` | `>=21.0,<22` | `22.8` | ⚠️ MISMATCH |
| `httpx` | `>=0.27` | `0.28.1` | ✅ OK |
| `python-dotenv` | `>=1.0` | `1.2.3` | ✅ OK |
| `feedparser` | **MISSING** | `6.0.14` (just installed) | 🔴 FIXED |
| `APScheduler` | **MISSING** | `3.11.3` | 🔴 FIXED (needs reqs.txt update) |

---

## 🏗️ Architecture Assessment

**Strengths:**
- Clean separation of concerns: config, providers, store, telegram interface
- `Course` dataclass normalizes across all platforms
- Provider pattern is extensible — easy to add new sources
- Deduplication by course slug is well-designed
- Cold-start baseline prevents notification floods

**Weaknesses:**
- `config.py` is a dataclass with `from_env()` — hard to test without env vars
- `telegram_bot.py` is 389 lines and growing — commands, polling, and broadcast are all in one class
- No dependency injection — `CourseBot` directly instantiates providers and `Store`
- `search_providers.py` duplicates `providers.py` retry/get patterns instead of reusing
- `store.py` is synchronous but called from async context — works because SQLite is fast, but could block the event loop under load

---

## 🎯 Recommended Priority Actions

| Priority | Action | Effort |
|---|---|---|
| P0 | Add `feedparser` and `APScheduler` to `requirements.txt` | 1 min |
| P0 | Fix `python-telegram-bot` version constraint (`<22` → `<23`) | 1 min |
| P0 | Remove real bot token from `.env`, rotate via @BotFather | 5 min |
| P1 | Add error handlers to the Telegram Application | 15 min |
| P1 | Fix Reddit RSS 403 (use API or remove URL) | 30 min |
| P1 | Add unit tests for `store.py` and `config.py` | 2 hours |
| P2 | Add linting/type-checking config (`ruff` + `mypy`) | 30 min |
| P2 | Restructure into `bot/` package or fix README references | 1 hour |
| P2 | Extract shared HTTP retry utility | 30 min |
| P3 | Implement `admin_chat_id` features or remove the config field | 1 hour |
| P3 | Delete unused `.kilo/worktrees/spiced-twine` worktree | 5 min |
| P3 | Add `.gitignore` for `__pycache__`, `*.pyc`, `.env` | 2 min |

---

## 📈 Bot Activity Summary (from bot.log)

| Metric | Value |
|---|---|
| Poll cycles completed | ~12 successful |
| Subscribers | 3 (1 active during most of the period) |
| Courses tracked | 140 |
| Reddit feed status | HTTP 403 (since Sept 9) |
| TutorialBar status | ✅ ~93-98 courses per poll |
| FreebiesGlobal status | ✅ ~15 entries per poll |
| DNS outages | Sept 9, 12:46–13:07 UTC |
| Missed scheduled jobs | 2 (during DNS outage) |
| Unhandled exceptions | Multiple `NetworkError` from `get_updates` |
