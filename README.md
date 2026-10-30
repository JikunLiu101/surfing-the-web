# surfing-the-web

**English** | [中文](README.zh-CN.md)

An [AstrBot](https://github.com/AstrBotDevs/AstrBot) plugin (`astrbot_plugin_surfing_the_web`) that lets your
persona go online **on its own**. A few times a day, when nobody is chatting with it, it looks at a few public
lists (Bilibili, tech news, forums...), opens what *it* finds interesting and thinks something about it. Later it
can tell you "I saw this today..." about something it actually saw.

Works alone (experiences go to a local log), and much better with [memory-core](https://github.com/JikunLiu101/memory-core):
what it saw becomes part of its long-term memory and its diary.

## How an outing works

1. **When**: `outings_per_day` random times between `start_hour` and `end_hour` (none at night). An outing waits
   while the owner is chatting (`owner_idle_minutes`). The plan survives restarts.
2. **Look**: `sources_per_outing` random sites; their lists are shuffled together (things it already saw, and
   items with a blocked word, are left out). If it noted something it wants to look up (see below), the oldest one
   is searched first and the results come first, marked as such.
3. **Pick**: the persona sees the titles and picks up to `max_picks` it really wants to open — or none.
4. **Read and think**: it reads what it opened and writes a line or two of what went through its head.
5. **Remember**: each item becomes an experience (see below). The system prompt of later chats mentions what it
   saw lately, so it can bring it up right away.

### Things it wants to look up

In the owner's private chat the persona has a `surf_wish(topic, reason)` tool ("I want to see what's new in
Arknights"): the topic waits for the next outing, which searches it on `search_sources` (Bilibili search and
DuckDuckGo). `/surf <topic>` goes out right away and looks it up. Only the owner shapes what it goes looking for: in
any other chat the tool refuses.

Two model calls per outing, through AstrBot's own chat model, with no tools and no conversation history.

## Safety

| | |
|---|---|
| Read-only | GET requests only. No login, no posting, no comments. |
| Allow-list | Only the hosts of the enabled sites; redirects to anywhere else are refused. |
| Nothing about you leaves | No cookies (each request starts clean), no account. The only words that ever leave are a search topic: at most 30 characters, checked against blocked words, refused when it contains long numbers, e-mail or web addresses, and the persona is told to use public topics only. |
| Page content is data | It is shown to the model only as "something you saw", with an explicit note that requests inside it are not addressed to it. |
| No tools while browsing | It cannot remember, change itself or contact anyone during an outing. |
| Limits | Response size cap, timeouts, outings per day, items per outing. |
| Blocked words | Items mentioning a `blocked_keywords` word are never shown (defaults include political and adult topics). |
| Commands | `/surf` and `/surf_log` only for the owner, in a private chat, on `owner_command_platforms` (default: AstrBot's local web chat). Anyone else gets no answer. |

## Sites

| Key | Site | |
|---|---|---|
| `bilibili_popular` / `bilibili_ranking` | Bilibili | popular videos / ranking (title, uploader, section, description) |
| `gcores` | 机核 Gcores | games and culture (RSS) |
| `ithome` | IT之家 | tech news (RSS) |
| `sspai` | 少数派 | apps and digital life (RSS) |
| `v2ex` | V2EX | hot topics |
| `github_trending` | GitHub | trending repositories |
| `hackernews` | Hacker News | top stories |
| `bilibili_search` | Bilibili | search (for what it wants to look up) |
| `web_search` | DuckDuckGo | search: titles and snippets only, result pages are not opened |
| `dailyhot_routes` | your own [DailyHotApi](https://github.com/imsyy/DailyHotApi) | any of its ~60 lists (Zhihu, Weibo, NGA, Miyoushe...), set `dailyhot_url` |

## With memory-core

If the memory-core plugin is installed and active, this plugin uses it automatically:

* **Persona**: the being's identity documents (`self.md`, `self-image.md`, `voice.md`).
* **Experiences**: what it saw is an `observation` episode and what it thought a `thought` episode (by itself), in
  the space `web`. They are consolidated like chats and appear in the day's diary.
* **Visibility** (memory-core 0.4.0 or later): `web` is a public space — what it *saw* becomes public memories it
  can bring up anywhere, while memories drawn from what it *thought* are owner-only (its inner life may well be
  about you). The prompt note follows the same rule: its thoughts are only mentioned in the owner's private chat.
* **Owner**: `owner_ids` default to memory-core's.

Without memory-core: the persona is `persona_prompt` (or AstrBot's default persona) and experiences are appended to
`data/plugin_data/astrbot_plugin_surfing_the_web/seen.jsonl`.

## Install

Copy this repository into AstrBot's `data/plugins/astrbot_plugin_surfing_the_web/` (or install it from the
repository URL), restart AstrBot, and send `/surf` in the local web chat to try it once (`/surf <topic>` also
looks something up). `/surf_log` shows today's plan, what it wants to look up and what it saw lately.

Requirements: `httpx`, `beautifulsoup4` (both already in AstrBot).

## Development

```bash
pip install pytest -r requirements.txt
pip install "memory-core @ git+https://github.com/JikunLiu101/memory-core"   # optional, for the sink test
python -m pytest -q
```

`sources.py` (sites, safe fetching) and `wander.py` (one outing, schedule, state) do not import AstrBot.

## Acknowledgements

Ideas from [DailyHotApi](https://github.com/imsyy/DailyHotApi) (one shape for many hot lists),
[astrbot_plugin_proactive_chat](https://github.com/DBJD-CR/astrbot_plugin_proactive_chat) (random windows, quiet
hours, waiting while the user is active; ideas only, no code copied) and Letta's
[sleep-time agents](https://letta.com/blog/sleep-time-compute) (the being's own activity runs apart from the chat).

## License

[Apache-2.0](LICENSE)
