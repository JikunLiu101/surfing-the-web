# Changelog

## 0.6.0 (2026-11-01)

- A dashboard (plugin view `surfing`, AstrBot 4.24.2+): today's outings and what is left, what she saw and
  thought, the last outings and their errors, what she can read now; send her out now (with a topic), add and
  remove wishes, log in to Bilibili by scanning a QR code on the page, check or remove the login. The login
  itself never goes to the page.

## 0.5.0 (2026-11-01)

- Market metadata: Chinese name 上网逛逛, `astrbot_version`, `support_platforms`, tags; an English
  translation of the name and description (`.astrbot-plugin/i18n/en-US.json`).

- Fewer prompt tokens: the full note of what she saw (with her thoughts and how to go online, about 1,000
  characters, never cached since it goes with the user's message) is sent with the first message of a talk
  (`recent_full_gap_minutes` of quiet, default 30) and whenever it changes; the later replies of the same talk get a
  short one (`brief_block`: titles and outings left). 0 sends the full note every time, as before.

## 0.4.0 (2026-10-31)

- When the owner asks the persona to go online, its own limits no longer stop it: `surf_now(..., asked=true)` is
  accepted when the owner's message in this private chat talks about going online, does not use up
  `self_outings_per_day` and skips the rest; such outings have their own ceiling (`asked_outings_per_day`,
  default 10) and gap (`asked_min_gap_minutes`, default 5). The prompt says so even when its own outings are used
  up, and `being_abilities` lists it as a separate ability (`surfing-the-web:asked`), so the persona notices it.
- Fix: the Bilibili QR login said "expired" right after the scan was confirmed. Bilibili now hands out the session
  in the Set-Cookie headers of the confirming poll, not in `data.url`, and a confirmed key answers "expired" when
  polled again. The login is now taken from Set-Cookie (or else from the URL, percent-encoded the way a browser
  sends it); if neither has it, the command says so and logs which names came back (never the values).
- Fix: videos opened at the same time each asked for a device id, and all but the first went without one and got
  412 from Bilibili. They now wait for one shared id.

- Opening something now reads it. Bilibili videos: their subtitles, with a Bilibili account the owner logs in by
  scanning a QR code (`/surf_bili_login`, `/surf_bili_status`, `/surf_bili_logout`); without subtitles, three
  frames from the progress-bar preview sheets, described by a vision model (`video_frames`, `vision_provider_id`,
  `frames_videos_per_outing`). Search results and items whose list says little: the page text through DeepSeek's
  Fetch API (`read_pages`). When nothing could be read she is told so (no more "an empty page").
- `web_search` is DeepSeek's Search API instead of DuckDuckGo, and is on by default. Search and Fetch use the key
  of AstrBot's DeepSeek provider, or `deepseek_api_key`.
- `SafeFetcher` can read bytes and POST JSON; credentials are dropped when a redirect leaves the host. Bilibili
  requests carry a device id (buvid3) handed out by Bilibili, without which video pages answer 412.
- The picked items are opened at the same time; content that turns out to contain a blocked word is closed.
- She knows what opening something gets her, and whom to ask when it breaks: `being_abilities` reports
  "上网时点开看内容" (with how to get back what is missing) and, while the Bilibili login works, a separate
  "看B站视频里说了什么", so memory-core lets her notice both getting it and losing it. A login Bilibili stops
  accepting is marked expired. What did not work on the last outing (login expired, DeepSeek balance or key, the
  vision model not answering) is told to her in the owner's private chat and in the `surf_now` result, with what
  the owner can do about it.

- Prompt caching: what she saw lately (`<surfing_the_web>`) goes with the user's message instead of the system
  prompt, and is taken out of the earlier messages in the history (`strip_blocks`), so the system prompt and the
  history stay the same between requests and the provider's prompt cache covers them.
- `being_abilities(owner)`: tells memory-core what going online lets her do (follows the settings), so she knows
  without being told.
- The rest after an outing drifts (`timing_jitter`, default give or take 20%) instead of always being
  `self_cooldown_minutes`.

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
