# Free Udemy Course Bot

A Telegram bot that checks for free Udemy courses on a timer and pushes new ones
to everyone subscribed. Users can filter by keyword or platform so they only
hear about topics they care about.

```
bot/               package
  __main__.py      `python -m bot` entry point (must live inside the package)
  main.py          startup: logging, Config, CourseBot, health, run_polling
  config.py        env-var config
  providers.py     where courses come from
  store.py         SQLite: subscribers, dedupe, digest queue, metrics, migrations
  telegram_bot.py  commands, inline mode, polling job, broadcast, digests
  search_providers.py  multi-platform search providers
  http_utils.py    shared HTTP retry logic
  rate_limit.py    per-user command rate limiting
  search_cache.py  short-lived TTL cache for search results
  metrics.py       in-process counters
  i18n.py          translations (en, es)
  shutdown.py      graceful shutdown coordination
  health.py        optional /healthz HTTP endpoint
setup.py           pip install -e .  (installs the `udemy-free-bot` script)
railway.json       Railway build/start config
Dockerfile         container build
docker-compose.yml one-command deploy
tests/             unit tests (114 tests)
```

> `python -m bot` resolves to `bot/__main__.py`, so that file has to be inside the
> package. A `__main__.py` at the project root is never picked up by `-m`; if the
> start command reports `No module named bot.__main__`, that file is missing.

## Two kinds of "free"

This matters more than anything else in the setup, so it's worth being clear:

**Permanently free courses** are queryable through Udemy's official Affiliate
API (`price=price-free`). Stable, legitimate, no scraping. The catch is that the
API needs an approved affiliate account, and the pool of genuinely free courses
is small and slow-moving.

**100%-off coupon deals** are what most "free Udemy course" channels actually
post. These are paid courses temporarily discounted to zero by the instructor,
usually valid for a few days or a few hundred redemptions. They do *not* show up
as free in the API. They come from coupon aggregators, several of which publish
RSS feeds.

Two providers handle the second case.

`TutorialBarProvider` is the default and needs no credentials. tutorialbar.com
publishes the coupons it tracks as structured data embedded in its pages, so the
provider reads that directly rather than scraping rendered HTML — one request
returns a dozen fully-populated courses, and fetching the front page plus each
category yields roughly 90 live courses per poll.

`RSSCouponProvider` adds breadth from coupon feeds. Point it at feeds you're
permitted to use and check each one's terms — some allow redistribution, some
don't. Be aware that many aggregators either block non-browser traffic or only
link to their own landing pages rather than to udemy.com, in which case they
contribute nothing; the logs report per-feed counts so you can tell.

Scraping udemy.com directly is against their Terms of Service and their bot
protection will block you anyway, so it isn't implemented here.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill it in
python -m bot
```

Or with Docker:

```bash
docker-compose up -d
```

You need at minimum a `TELEGRAM_BOT_TOKEN` from [@BotFather](https://t.me/BotFather).
Everything else has working defaults: `TUTORIALBAR=true` is on out of the box, so
the bot finds courses without any credentials. Add Udemy API credentials or
coupon feed URLs to widen coverage.

The first poll is a cold start: it records everything it finds as a baseline
without announcing it, so you don't get ninety messages as a greeting. From then
on new courses go out at up to `MAX_PER_CYCLE` per poll, and anything over that
cap is announced on following polls rather than dropped.

## Commands

| Command | What it does |
|---|---|
| `/start` | Subscribe |
| `/stop` | Unsubscribe |
| `/filter python, design` | Only receive courses matching these keywords |
| `/filter off` | Clear the filter (get everything) |
| `/freq instant\|daily\|weekly\|paused` | Set notification frequency |
| `/latest` | Last 5 finds |
| `/latest coursera` | Last 5 finds from a platform |
| `/course <slug>` | Course details by slug or URL |
| `/categories` | Browse course categories |
| `/search python` | Search courses across every configured platform |
| `/search python coursera` | Search a specific platform |
| `/status` | Sources, platforms, interval, counts, metrics |
| `/help` | Show all commands |

## Notification frequency

By default every new course is sent immediately. You can change that:

| Frequency | Behaviour |
|---|---|
| `instant` | Get each course as it's found (default) |
| `daily` | Receive a bundled digest once a day at ~09:00 UTC |
| `weekly` | Receive a bundled digest every Monday at ~10:00 UTC |
| `paused` | No notifications (courses still queued for later) |

```
/freq daily      # switch to daily digest
/freq instant    # back to immediate
/freq paused     # mute
```

Digest subscribers' courses are queued in the `digest_queue` table and
flushed by scheduled jobs. Switching frequency mid-day keeps queued courses —
they'll appear in the next digest.

## Keyword filtering

When you subscribe you get every course. Set a keyword filter to narrow it:

- `/filter python, design` — only courses whose title, instructor, level, or
  tags contain **any** of those keywords
- `/filter off` — receive everything again

## Inline mode

You can also search from any chat by mentioning the bot inline:

```
@BotName python course
```

Returns up to 5 matching courses as inline results that you can send directly.

## Rate limiting

Each user is limited to 10 commands per 60 seconds. Exceeding the limit
returns a polite "try again in Ns" message. The limiter is per-chat and uses
a sliding window, so bursts are smoothed naturally.

## Search caching

`/search` results are cached for 60 seconds per query+platform key, so
repeated or overlapping queries don't re-crawl catalogue pages. Inline-query
results use the same cache.

## Webhook mode

Set `WEBHOOK_URL` (and optionally `WEBHOOK_PORT`) to receive updates via
webhook instead of long-polling. Useful behind a reverse proxy or when
long-polling is unreliable:

```bash
WEBHOOK_URL=https://your-domain.com
WEBHOOK_PORT=8443
```

When `WEBHOOK_URL` is unset the bot uses long-polling (the default).

## Health check

Set `HEALTH_PORT` to expose a `GET /healthz` endpoint returning JSON stats:

```bash
HEALTH_PORT=8080
# curl http://localhost:8080/healthz
# {"status":"ok","subscribers":3,"courses":140,"digest_queued":0,"uptime_s":3600}
```

Disabled by default (unset or 0).

## Graceful shutdown

SIGINT/SIGTERM trigger an orderly shutdown: in-flight broadcasts finish,
the health server stops, and the process exits cleanly. The `ShutdownManager`
runs registered callbacks in order.

## Multi-language

User-facing strings are translated via `bot/i18n.py`. English and Spanish
are included; the bot's `LANGUAGE` env var sets the default. Missing keys
fall back to English. Add a language by adding a dict to `_TRANSLATIONS`.

## Database migrations

Schema changes run automatically on startup. `PRAGMA user_version` tracks
the current version; each pending migration in `MIGRATIONS` is applied in
order, then the version is stamped to `LATEST_SCHEMA_VERSION`. Existing
data is preserved across migrations.

## Metrics

In-memory counters track polls, searches, messages sent, cache hits/misses,
and per-command usage. Visible in `/status` and on `/healthz`. The `metrics`
table in SQLite provides persistence across restarts via `bump_metric()`.

## Design notes

**Dedupe** is on course ID, stored forever (well, 90 days — a daily job prunes
older rows). Coupon links dedupe on the course *slug* rather than the full URL,
because coupon codes rotate and you don't want the same course re-announced
every time a new code appears.

**First-run flood protection**: every new course is written to the `seen` table
immediately, but only `MAX_PER_CYCLE` (default 10) are actually broadcast. So
the first poll records the backlog silently instead of firing 300 messages at
your first subscriber.

**Rate limits**: Telegram permits roughly 30 messages/second across different
chats. The broadcast loop uses exponential backoff (up to 3 retries) and
a 60ms delay between sends. Users who block the bot raise `Forbidden` and
are removed from the subscriber table automatically.

**Polling interval** defaults to hourly. Don't drop it much below that —
coupon feeds don't update faster than that anyway, and hammering them is a good
way to get your IP blocked.

## Deploying

### Railway

`railway.json` pins the build to the `Dockerfile` and the start command to
`python -m bot`, which runs the `bot` package's `__main__.py`. If you set a
Start Command in the service settings instead, make it exactly `python -m bot`
(or `python -m bot.main`); `python -m bot/` and `python __main__.py` do not
resolve and exit with `No module named bot.__main__`, which Railway then retries
in a restart loop.

Set `TELEGRAM_BOT_TOKEN` as a service variable — the bot exits immediately if it
is missing (`TELEGRAM_BOT_TOKEN is not set` in the logs).

Check the volume mount path too. A volume mounted at `/app` shadows the freshly
built code with whatever was in the volume from the first deploy, so code fixes
never reach the container — the bot would keep failing with the old error no
matter how many times you redeploy. Mount the volume somewhere else (for example
`/data`) and set `DB_PATH=/data/bot.db` so the SQLite file is what persists and
the code keeps coming from the image. A service can only have one volume, so
replace the `/app` one rather than adding a second.

### With Docker Compose

```bash
docker-compose up -d
```

The `bot.db` file lives on the `bot-data` volume (`DB_PATH=/data/bot.db`) so you
keep your subscribers on every deploy, while the code keeps coming from the
image — mounting a volume at `/app` would pin you to the first deploy's source.
Only run one instance; two processes long-polling the same token will fight over
updates and duplicate every message.

### Systemd

```ini
[Unit]
Description=Udemy free course bot
After=network-online.target

[Service]
WorkingDirectory=/opt/udemy-free-bot
ExecStart=/opt/udemy-free-bot/.venv/bin/python -m bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### pip install

```bash
pip install -e .
udemy-free-bot
```

## Extending

Adding a source means subclassing `Provider` and returning `Course` objects —
the bot doesn't care where they came from:

```python
class MyProvider(Provider):
    name = "my-source"

    async def fetch(self) -> list[Course]:
        ...
```

Then append it to `self.providers` in `CourseBot.__init__`.

Swapping Telegram for Discord or email means replacing `telegram_bot.py` only;
`providers.py` and `store.py` are transport-agnostic. The pieces you'd need to
reimplement are subscribe/unsubscribe, the timer that calls `fetch_all()` +
`filter_new()`, and a send loop.

## Affiliate links

If you're monetising, don't hand-build tracking URLs — Udemy routes affiliate
traffic through Impact deep links, and a malformed link just loses the
attribution. Generate them in the Impact dashboard and wrap `course.url` in
`format_course()`.


## Multi-platform search

`/search <query>` fans out across every configured platform concurrently and
merges the results, labelled by platform. You can filter by platform too:
`/search python coursera`. The Udemy sources that drive the hourly broadcast
take part too, so `/search` covers Udemy plus the rest.

| Platform | How it is reached | Search quality |
|---|---|---|
| Udemy | the existing coupon providers | good (local match over the live coupon set) |
| MIT OpenCourseWare / MITx | `api.learn.mit.edu` search API | best - real relevance ranking |
| Coursera | public catalogue, paged | partial - see below |
| edX | public course list, paged | partial - see below |
| freeCodeCamp | published tutorial RSS | recent posts only |

Coursera and edX publish readable catalogues but no usable search: Coursera's
documented `q=search` finder answers *"finder 'search' not implemented"*, and
edX's `search_term` is silently ignored (it returns the same result count for a
real query, a nonsense query, and no query at all). So the bot scans a bounded
window of their catalogues - `SEARCH_CATALOGUE_PAGES` x 100 entries - and
matches locally. **Recall from those two is therefore partial**: raising the
page count improves it at the cost of a slower first search.

The catalogue crawl is cached for ten minutes, so the first search costs about
twenty seconds and subsequent ones - including new queries - take about three.

A provider that fails, times out, or is switched off is logged and skipped; the
other platforms' results are still returned.

Class Central, Khan Academy, FutureLearn, Pluralsight and LinkedIn Learning are
**not** implemented: the first three publish no usable API (Khan Academy's old
open API now answers `410 Gone`), and the last two are partner programmes whose
credentials are not self-serve. Nothing is scraped around a login, paywall or
bot protection to reach them.

## Development

```bash
pip install -r requirements.txt
pip install ruff pytest pytest-cov
ruff check .
pytest tests/ -v
pytest tests/ --cov=bot --cov-report=term-missing   # with coverage
```

CI runs ruff, mypy (non-blocking), and pytest with `--cov-fail-under=60`.
