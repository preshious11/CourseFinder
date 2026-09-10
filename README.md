# Free Udemy Course Bot

A Telegram bot that checks for free Udemy courses on a timer and pushes new ones
to everyone subscribed. Users can filter by keyword so they only hear about
topics they care about.

```
main.py              entry point
bot/config.py        env-var config
bot/providers.py     where courses come from
bot/store.py         SQLite: subscribers + dedupe
bot/telegram_bot.py  commands, polling job, broadcast
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
cd ..
python -m bot.main
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
| `/filter off` | Clear the filter |
| `/latest` | Last 5 finds |
| `/search python` | Search courses across every configured platform |
| `/status` | Sources, search platforms, interval, counts |

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
chats. There's a 60ms delay between sends and a `RetryAfter` handler that sleeps
and retries once. Users who block the bot raise `Forbidden` and are removed from
the subscriber table automatically.

**Polling interval** defaults to hourly. Don't drop it much below that —
coupon feeds don't update faster than that anyway, and hammering them is a good
way to get your IP blocked.

## Deploying

Any always-on host works: a €4 VPS, Railway, Fly.io, a Raspberry Pi. Two things
to get right:

1. `bot.db` must live on a persistent volume, or you lose your subscriber list
   on every redeploy.
2. Run exactly one instance. Two processes long-polling the same token will
   fight over updates and duplicate every message.

A minimal systemd unit:

```ini
[Unit]
Description=Udemy free course bot
After=network-online.target

[Service]
WorkingDirectory=/opt/udemy-free-bot
ExecStart=/opt/udemy-free-bot/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
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
merges the results, labelled by platform. The Udemy sources that drive the
hourly broadcast take part too, so `/search` covers Udemy plus the rest.

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
