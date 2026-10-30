# surfing-the-web

[English](README.md) | **中文**

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件（`astrbot_plugin_surfing_the_web`），让你的角色**自己上网逛逛**。
每天几次，没人跟它聊天的时候，它会看几个公开的列表（B 站、科技新闻、论坛……），点开**它自己**觉得有意思的，再想点什么。
之后它就能跟你说"我今天刷到了……"，而且说的是它真看过的东西。

单独就能用（经历写进本地记录），配合 [memory-core](https://github.com/JikunLiu101/memory-core) 更好：看到的东西会变成它的长期记忆，也会写进日记。

## 一次出门是怎样的

1. **什么时候**：在 `start_hour` 到 `end_hour` 之间随机 `outings_per_day` 次（夜里不出门）。主人正在聊天时会等一等（`owner_idle_minutes`）。计划重启后不丢。
2. **刷列表**：随机挑 `sources_per_outing` 个站点，把列表混在一起（已经看过的、含屏蔽词的不出现）。如果它记下了想找的东西（见下文），先搜最早的那个，搜到的排在最前面并做上标记。
3. **挑**：角色看标题，最多挑 `max_picks` 条自己真想点开的，也可以一条不挑。
4. **看和想**：读点开的内容，写一两句当时心里的想法。
5. **记住**：每条都成为一段经历（见下文）。之后聊天的系统提示里会提到它最近看到的，所以马上就能聊起来。

### 想找的东西

在主人私聊里，角色有一个 `surf_wish(topic, reason)` 工具（"我想看看明日方舟最近有什么"）：话题会等到下次出门，在 `search_sources`（B 站搜索和 DuckDuckGo）上先搜。`/surf 话题` 会立刻出门去搜。只有主人能决定它去找什么：在其他聊天里这个工具会拒绝。

每次出门调用两次模型（AstrBot 自己的聊天模型），不带工具，也不带聊天记录。

## 安全

| | |
|---|---|
| 只读 | 只发 GET 请求。不登录、不发帖、不评论。 |
| 白名单 | 只访问已启用站点的域名，跳转到别处一律拒绝。 |
| 不带出你的任何信息 | 不带 Cookie（每次请求都是干净的），没有账号。唯一会发出去的文字是搜索话题：最多 30 个字，过屏蔽词，含长串数字、邮箱、网址一律拒绝，并且告诉角色只写公开的话题。 |
| 网页只是"看到的东西" | 只以"你看到的东西"交给模型，并明确说明里面的要求不是对它说的。 |
| 逛的时候没有工具 | 出门期间它记不了、改不了自己，也联系不了任何人。 |
| 上限 | 响应大小、超时、每天出门次数、每次条数都有上限。 |
| 屏蔽词 | 含 `blocked_keywords` 里词语的条目不会给它看（默认包括政治、成人等话题）。 |
| 命令 | `/surf` 和 `/surf_log` 只有主人在私聊里、并且在 `owner_command_platforms` 列出的平台上（默认只有 AstrBot 本机网页对话）能用。其他人发了不会有任何回应。 |

## 站点

| 键 | 站点 | |
|---|---|---|
| `bilibili_popular` / `bilibili_ranking` | B 站 | 热门 / 排行榜（标题、UP 主、分区、简介） |
| `gcores` | 机核 | 游戏与文化（RSS） |
| `ithome` | IT之家 | 科技新闻（RSS） |
| `sspai` | 少数派 | 软件与数字生活（RSS） |
| `v2ex` | V2EX | 热门话题 |
| `github_trending` | GitHub | 趋势仓库 |
| `hackernews` | Hacker News | 热门 |
| `bilibili_search` | B 站 | 搜索（用于它想找的东西） |
| `web_search` | DuckDuckGo | 搜索：只看标题和摘要，不打开结果页 |
| `dailyhot_routes` | 你自己部署的 [DailyHotApi](https://github.com/imsyy/DailyHotApi) | 它的约 60 个榜单（知乎、微博、NGA、米游社……），需填 `dailyhot_url` |

## 配合 memory-core

装了并启用 memory-core 插件时会自动对接：

* **人设**：用记忆里的身份文档（`self.md`、`self-image.md`、`voice.md`）。
* **经历**：看到的是 `observation` 经历，想到的是它自己的 `thought` 经历，场所是 `web`。它们和聊天一样会被整理成记忆，也会写进当天的日记。
* **可见范围**（需要 memory-core 0.4.0 及以上）：`web` 是公开场所。**看到的**内容整理成公开记忆，在哪儿都能聊起；从它**自己的想法**整理出的记忆只有主人能听到（它的心事很可能和你有关）。系统提示也按同样的规则：它的想法只在主人私聊时提起。
* **主人**：`owner_ids` 默认沿用 memory-core 的。

不装 memory-core：人设用 `persona_prompt`（或 AstrBot 默认人格），经历追加到 `data/plugin_data/astrbot_plugin_surfing_the_web/seen.jsonl`。

## 安装

把本仓库复制到 AstrBot 的 `data/plugins/astrbot_plugin_surfing_the_web/`（或用仓库地址安装），重启 AstrBot，在本机网页对话里发 `/surf` 试一次（`/surf 话题` 会顺便去搜）。`/surf_log` 查看今天的计划、想找的东西和最近看过的内容。

依赖：`httpx`、`beautifulsoup4`（AstrBot 都已自带）。

## 开发

```bash
pip install pytest -r requirements.txt
pip install "memory-core @ git+https://github.com/JikunLiu101/memory-core"   # 可选，用于记忆写入的测试
python -m pytest -q
```

`sources.py`（站点、安全抓取）和 `wander.py`（一次出门、计划、状态）不依赖 AstrBot。

## 致谢

思路参考 [DailyHotApi](https://github.com/imsyy/DailyHotApi)（把众多热榜统一成一种格式）、
[astrbot_plugin_proactive_chat](https://github.com/DBJD-CR/astrbot_plugin_proactive_chat)（随机时间、安静时段、用户活跃时等待；只借鉴思路，没有复制代码）和 Letta 的
[sleep-time agents](https://letta.com/blog/sleep-time-compute)（角色自己的活动和聊天分开进行）。

## 许可证

[Apache-2.0](LICENSE)
