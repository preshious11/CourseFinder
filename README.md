# Free Udemy Course Bot

A Telegram bot that checks for free Udemy courses on a timer and pushes new ones
to everyone subscribed. Users can filter by keyword or platform so they only
hear about topics they care about.

```
bot/               package
  main.py          entry point (run with python -m bot)
  config.py        env-var config
  providers.py     where courses come from
  store.py         SQLite: subscribers + dedupe
  telegram_bot.py  commands, inline mode, polling job, broadcast
  search_providers.py  multi-platform search providers
__main__.py        python -m bot support
setup.py           pip install -e .
Dockerfile         container build
docker-compose.yml one-command deploy
tests/             unit tests
```

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
| `/latest` | Last 5 finds |
| `/search python` | Search courses across every configured platform |
| `/search python coursera` | Search a specific platform |
| `/status` | Sources, search platforms, interval, counts |
| `/help` | Show all commands |

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

### With Docker Compose

```bash
docker-compose up -d
```

The `bot.db` volume is mounted persistently so you keep your subscribers on
every deploy. Only run one instance — two processes long-polling the same token
will fight over updates and duplicate every message.

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
pip install ruff pytest
ruff check .
pytest tests/ -v
```
