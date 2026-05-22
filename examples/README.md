# swoop examples

These are real-world usage patterns. Run any of them with `python examples/<file>.py` after `pip install swoop-flights[validation]`.

Each script uses only the public API from `swoop.__all__`. No CLI, no extra dependencies, no hidden globals. Copy any of them into your own project and adapt.

## Scripts

- **`price_drop_watcher.py`** — Watch a known flight for price drops on a schedule, cache the last-seen price to disk, and print when the price falls. Similar in spirit to what [Perch](https://perchtravel.com) uses in production to save users an average of $247 per trip.
- **`multi_city_finder.py`** — Run an official multi-city / open-jaw search across arbitrary legs, show the top 5 trip options with per-leg details, and tune the beam search knobs (`max_results`, `beam_width`, `time_budget`).
- **`deals_watcher.py`** — Discovery-style watcher: run a `swoop.deals()` query (with the full filter surface — region, budget, trip-length, depart-window, discount), diff against the prior run, and print new deals + price changes. Mirrors the single-watcher-per-cache-file pattern from `price_drop_watcher.py` but at the exploration layer.

## Notes

Google rate-limits aggressively. The scripts catch `SwoopRateLimitError` and back off. If you see repeated 429s, slow your polling interval or set a proxy via `swoop.set_proxy()`.

Prices are integers in the currency's major unit. `PriceResult.currency` and `TripOption.currency` give you the ISO 4217 code.
