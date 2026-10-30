# Changelog

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
