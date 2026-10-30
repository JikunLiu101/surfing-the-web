# Changelog

## 0.3.1 (2026-10-30)

- Search defaults to Bilibili only; `web_search` (DuckDuckGo) stays available but is off by default: its snippets
  (often wiki index pages) were too short to be interesting.

## 0.3.0 (2026-10-30)

- The persona can go online by itself: the `surf_now(topic, reason)` tool (owner's private chat only) goes out
  right away, optionally looks a topic up, and returns what it saw and thought. So that it cannot be talked into
  going again and again: `self_outings_per_day` (default 3) and a rest of `self_cooldown_minutes` (default 60) after
  any outing; the prompt tells it how many are left. Scheduled outings also wait out the rest.

## 0.2.0 (2026-10-30)

- Things to look up: in the owner's private chat the persona can note a topic with the `surf_wish` tool; the next
  outing searches it first (Bilibili search, DuckDuckGo titles and snippets) and marks the results. `/surf <topic>`
  goes out now and looks it up. Topics: at most 30 characters, no long numbers, e-mail or web addresses, no blocked
  words; only the topic words are sent. Outside the owner's private chat the tool refuses.
- Outing reports and `/surf_log` show which sites were visited and what was looked up.

## 0.1.0 (2026-10-30)

First release.

- A few outings a day at random times (not at night, not while the owner is chatting): the persona looks at a
  few lists, picks what it likes, reads it and thinks something about it.
- Built-in sites: Bilibili (popular, ranking), Gcores, IT之家, 少数派, V2EX, GitHub Trending, Hacker News; any
  list of a self-hosted DailyHotApi.
- Safe fetching: GET only, allow-listed hosts (redirects included), no cookies, size-capped; page content is
  given to the model as something seen, never as instructions; no tools while browsing; blocked words.
- With memory-core: experiences become `observation` / `thought` episodes in the public space `web`. Without
  it: a local log.
- Owner commands `/surf`, `/surf_log` (owner, private chat, local web chat only by default).
