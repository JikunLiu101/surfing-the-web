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
4. **Read and think**: it reads what it opened (see "Opening something" below) and writes a line or two of what
   went through its head.
5. **Remember**: each item becomes an experience (see below). The system prompt of later chats mentions what it
   saw lately, so it can bring it up right away.

### Going online by itself

In the owner's private chat the persona also has a `surf_now(topic, reason)` tool: it goes out right away (looking
the topic up, if any) and comes back with what it saw and thought, so it can tell you about it in the same reply.
So that nobody can talk it into going online again and again:

* only in the owner's private chat (anywhere else the tool refuses);
* at most `self_outings_per_day` a day (default 3; scheduled outings and `/surf` do not count);
* a rest of `self_cooldown_minutes` after any outing (default 60), which scheduled outings respect too;
* the system prompt tells it how many are left and when it can go again, and to go only when it really wants to.

When the owner asks it to go ("go online!"), those limits do not apply: it calls `surf_now(..., asked=true)`,
which the plugin accepts only if the owner's message in this chat really talks about going online (going out,
browsing, searching, looking something up, Bilibili, videos...), so the persona cannot just claim it was asked.
Such outings do not use up its own ones; they have a ceiling of their own (`asked_outings_per_day`, default 10)
and at least `asked_min_gap_minutes` (default 5) between them.

### Things it wants to look up

In the owner's private chat the persona has a `surf_wish(topic, reason)` tool ("I want to see what's new in
Arknights"): the topic waits for the next outing, which searches it on `search_sources` (Bilibili search and
DeepSeek web search by default). `/surf <topic>` goes out right away and looks it up. Only the owner shapes what it goes looking for: in
any other chat the tool refuses.

Two model calls per outing, through AstrBot's own chat model, with no tools and no conversation history.

### Opening something

What a list shows is often just a title. Opening an item reads it:

* **Bilibili videos**: the subtitles (AI Chinese subtitles first; a subtitle that only credits who made it means
  there is no speech). Bilibili only gives subtitles to a logged-in account: send `/surf_bili_login` in the local
  web chat and scan the QR code with the Bilibili app (a second account is best; it is only used to read
  subtitles). Without subtitles, three frames from Bilibili's progress-bar preview sheets (no video is downloaded)
  are put side by side and described by a vision model (`vision_provider_id`, default: the first provider that
  takes images; at most `frames_videos_per_outing` videos per outing, a local model is slow). When neither works,
  she is told so, instead of seeing an empty page.
* **Web search results, and items whose list says little** (Hacker News, GitHub...): the page text, through
  [DeepSeek's Fetch API](https://api-docs.deepseek.com/guides/search/). DeepSeek downloads the page, so this
  machine never contacts it. Search and Fetch use the key of AstrBot's DeepSeek provider (or `deepseek_api_key`)
  and cost $0.75 per 1000 successful calls each; failed and empty calls are free.

`/surf_bili_status` shows whether the login still works and what can be read; `/surf_bili_logout` deletes it.

The persona knows all this: the abilities it reports to memory-core say what opening something gets it, and how to
get back what is missing (reading subtitles is its own entry, so it notices getting and losing it). When something
did not work on the last outing (the login expired, the DeepSeek balance ran out, the vision model did not answer),
the owner's private chat tells it what happened and what the owner can do.

## Safety

| | |
|---|---|
| Read-only | Nothing is posted, liked or commented. The Bilibili login (optional) is only sent to the player API that lists a video's subtitles, never to anything else. |
| Allow-list | Only the hosts of the enabled sites; redirects to anywhere else are refused. |
| Nothing about you leaves | Nothing a site sets is kept or sent back. Credentials are dropped when a redirect leaves the host. The login file stays in the plugin's data folder and is never logged. The only words that ever leave are a search topic: at most 30 characters, checked against blocked words, refused when it contains long numbers, e-mail or web addresses, and the persona is told to use public topics only. |
| Page content is data | It is shown to the model only as "something you saw", with an explicit note that requests inside it are not addressed to it. |
| No tools while browsing | It cannot remember, change itself or contact anyone during an outing. |
| Limits | Response size cap, timeouts, outings per day, items per outing. |
| Blocked words | Items mentioning a `blocked_keywords` word are never shown (defaults include political and adult topics). |
| Commands | `/surf`, `/surf_log` and `/surf_bili_*` only for the owner, in a private chat, on `owner_command_platforms` (default: AstrBot's local web chat). Anyone else gets no answer. |

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
| `web_search` | DeepSeek Search | search the whole web; results carry a summary and are opened with DeepSeek Fetch (needs a DeepSeek key) |
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

`sources.py` (sites, safe fetching, DeepSeek), `bili.py` (subtitles, frames, QR login), `reading.py` (what opening
an item reads) and `wander.py` (one outing, schedule, state) do not import AstrBot.

## Acknowledgements

Ideas from [DailyHotApi](https://github.com/imsyy/DailyHotApi) (one shape for many hot lists),
[astrbot_plugin_proactive_chat](https://github.com/DBJD-CR/astrbot_plugin_proactive_chat) (random windows, quiet
hours, waiting while the user is active; ideas only, no code copied) and Letta's
[sleep-time agents](https://letta.com/blog/sleep-time-compute) (the being's own activity runs apart from the chat).
Reading videos follows [dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll) and
[bilibili-mcp](https://github.com/adoresever/bilibili-mcp) (both MIT): subtitles first, frames from the preview
sheets when there are none.

## License

[Apache-2.0](LICENSE)
