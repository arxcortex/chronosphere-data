# Games data

Read by chronosphere.html (Play menu). The page carries built-in fallbacks, so missing files never break it.

- `daily_pool.json`: event slugs eligible for Chronle. The day's event is `events_sorted_by_slug[fnv1a("chronle:" + UTC date) % n]`, so every player gets the same puzzle. A slug that no longer matches an event is skipped; if fewer than 20 match, the page uses every History event.
- `tours.json`: guided five-stop tours. Each stop: `lens`, `year`, `lat`, `lon`, `caption`, optional `event` slug (opens that event's panel) and `title`.

Event slugs are `<geo|religion>-<name slug>-<year>`, the same id embeds.json uses and the 4th segment of the deep link `#history/1517/globe/geo-...`.
