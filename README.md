# 上网逛逛（surfing-the-web）

[English](README.en.md) | **中文**

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件（`astrbot_plugin_surfing_the_web`），让你的角色**自己上网逛逛**。
每天几次，没人跟它聊天的时候，它会看几个公开的列表（B 站、科技新闻、论坛……），点开**它自己**觉得有意思的，再想点什么。
之后它就能跟你说"我今天刷到了……"，而且说的是它真看过的东西。

单独就能用（经历写进本地记录），配合[记忆核心](https://github.com/JikunLiu101/astrbot_plugin_memory_core)（memory-core）更好：看到的东西会变成它的长期记忆，也会写进日记。

## 一次出门是怎样的

1. **什么时候**：在 `start_hour` 到 `end_hour` 之间随机 `outings_per_day` 次（夜里不出门）。主人正在聊天时会等一等（`owner_idle_minutes`）。计划重启后不丢。
2. **刷列表**：随机挑 `sources_per_outing` 个站点，把列表混在一起（已经看过的、含屏蔽词的不出现）。如果它记下了想找的东西（见下文），先搜最早的那个，搜到的排在最前面并做上标记。
3. **挑**：角色看标题，最多挑 `max_picks` 条自己真想点开的，也可以一条不挑。
4. **看和想**：读点开的内容（见下文"点开以后"），写一两句当时心里的想法。
5. **记住**：每条都成为一段经历（见下文）。之后聊天的系统提示里会提到它最近看到的，所以马上就能聊起来。

### 自己上网

在主人私聊里，角色还有一个 `surf_now(topic, reason)` 工具：马上出门（有话题就顺便去搜），回来带着看到的和想到的，同一条回复里就能讲给你听。为了不被人一句接一句地劝着出门：

* 只在主人私聊里能用（其他地方工具会拒绝）；
* 每天最多 `self_outings_per_day` 次（默认 3 次；定时出门和 `/surf` 不算）；
* 任何一次出门之后要歇 `self_cooldown_minutes` 分钟（默认 60），定时出门也会等；
* 系统提示会告诉它今天还剩几次、什么时候能再去，并提醒它想去才去。

主人亲口让它去（"去上网吧"）时不受这些限制：它调用 `surf_now(..., asked=true)`，插件会核对主人在这个私聊里的这句话确实说到了上网（出门、逛、搜、查、看看、B站、视频之类），对不上就不算，所以它没法自称"是主人让我去的"。这样的出门不占它自己的次数，另有上限（`asked_outings_per_day`，默认 10 次），两次之间至少隔 `asked_min_gap_minutes` 分钟（默认 5）。`surf_now` 最多等 `surf_now_wait_seconds` 秒（默认 110，要比 AstrBot 的工具调用超时短）；看得久的出门不会白逛，会接着进行、照样记进记忆，只是先告诉它“还在看”。

### 想找的东西

在主人私聊里，角色有一个 `surf_wish(topic, reason)` 工具（"我想看看明日方舟最近有什么"）：话题会等到下次出门，在 `search_sources`（默认 B 站搜索和 DeepSeek 网页搜索）上先搜。`/surf 话题` 会立刻出门去搜。只有主人能决定它去找什么：在其他聊天里这个工具会拒绝。

每次出门调用两次模型（AstrBot 自己的聊天模型），不带工具，也不带聊天记录。

### 点开以后

列表上往往只有一个标题。点开一条会去读它的内容：

* **B 站视频**：读字幕（优先 AI 中文字幕；只有"字幕 by 某某"这种署名的，说明视频里没人说话）。B 站只给登录的账号看字幕：在本机网页对话里发 `/surf_bili_login`，用手机 B 站扫码确认（建议用小号，只用来读字幕）。B 站常把别的视频的 AI 字幕给过来，只用文件名对得上这个视频（以它的 aid + cid 开头）的，对不上就再问，最多 5 次。没有字幕时：
  * **听**：下载视频的音频（最小的那档，只要前 `listen_minutes` 分钟，约每分钟 0.5 MB），在本机用 PyAV 解码、用 SenseVoice（[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)）听写成文字，还会听出背景音乐、笑声、掌声；每次出门最多听 `listen_videos_per_outing` 个。几分钟的视频几秒就听完。要装 `av`、`sherpa-onnx`，并把模型（[sherpa-onnx 版 SenseVoice](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17) 的 `model.int8.onnx` 和 `tokens.txt`，约 240 MB）放进 `data/plugin_data/astrbot_plugin_surfing_the_web/models/sense-voice/`（或 `listen_model_dir`）；没装就不听。
  * **看**：从 B 站进度条预览图里截 9 个画面（不下载视频）：首尾各留一小段（时长的 5%，3 到 15 秒），从开头那段结束截到结尾那段开始，中间平均分 8 份（张数和缓冲都能在设置里改：`frames_per_video`、`frames_buffer_percent`、`frames_buffer_min_seconds`、`frames_buffer_max_seconds`）；拼成 3×3 的格子、每格标上时间，交给能看图的模型按时间讲发生了什么、抄下画面上的字（`vision_provider_id`，默认用第一个支持图片的模型；每次出门最多看 `frames_videos_per_outing` 个视频；几个视频的画面排队一个一个看，`vision_timeout` 每张单独算、从轮到它开始计时，默认 90 秒）。

什么都拿不到时会如实告诉她，不会让她以为是空页面。记进记忆的是她写感想时顺带写的一句话概括，不是内容的开头。
* **网页搜索结果，以及列表上没什么内容的条目**（Hacker News、GitHub……）：通过 [DeepSeek 的 Fetch 接口](https://api-docs.deepseek.com/guides/search/) 读正文。网页由 DeepSeek 去下载，本机不会访问那个网站。Search 和 Fetch 用 AstrBot 里 DeepSeek 服务商的 key（或者 `deepseek_api_key`），每 1000 次成功调用各 0.75 美元，失败和没结果的不收费。

`/surf_bili_status` 查看登录是否还有效、哪些内容读得了；`/surf_bili_logout` 删除登录。

角色自己知道这些：插件报给 memory-core 的本事里写明了点开能读到什么、缺了什么该找谁补（"读字幕"单独一项，所以有了、没了它都会察觉）。上次出门有什么不顺（登录过期、DeepSeek 余额不够、看图模型没反应），在主人私聊里会告诉它出了什么事、主人能怎么解决。

## 安全

| | |
|---|---|
| 只读 | 不发帖、不点赞、不评论。B 站登录（可选）只发给列出视频字幕的那个播放器接口，不发给别处。 |
| 白名单 | 只访问已启用站点的域名，跳转到别处一律拒绝。 |
| 不带出你的任何信息 | 网站设置的 Cookie 一律不保存、不回传。跳转到别的网站时会去掉登录和 key。登录文件只放在插件的数据目录里，不写进日志。唯一会发出去的文字是搜索话题：最多 30 个字，过屏蔽词，含长串数字、邮箱、网址一律拒绝，并且告诉角色只写公开的话题。 |
| 网页只是"看到的东西" | 只以"你看到的东西"交给模型，并明确说明里面的要求不是对它说的。 |
| 逛的时候没有工具 | 出门期间它记不了、改不了自己，也联系不了任何人。 |
| 上限 | 响应大小、超时、每天出门次数、每次条数都有上限。 |
| 屏蔽词 | 含 `blocked_keywords` 里词语的条目不会给它看（默认包括政治、成人等话题）。 |
| 命令 | `/surf`、`/surf_log` 和 `/surf_bili_*` 只有主人在私聊里、并且在 `owner_command_platforms` 列出的平台上（默认只有 AstrBot 本机网页对话）能用。其他人发了不会有任何回应。 |

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
| `web_search` | DeepSeek Search | 全网搜索，结果带摘要，点开用 DeepSeek Fetch 读正文（需要 DeepSeek key） |
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

依赖：`httpx`、`beautifulsoup4`（AstrBot 都已自带）。听视频（可选）：`pip install av sherpa-onnx`，再放好上面说的模型。

## 开发

```bash
pip install pytest -r requirements.txt
pip install memory-core-lib   # 可选，用于记忆写入的测试
python -m pytest -q
```

`sources.py`（站点、安全抓取、DeepSeek）、`bili.py`（字幕、画面、扫码登录）、`reading.py`（点开以后读什么）和 `wander.py`（一次出门、计划、状态）不依赖 AstrBot。

## 后台面板

AstrBot 4.24.2 起，后台「插件 → 上网逛逛 → 视图 → 上网」：今天的出门计划、她自己去了几次、主人让她去了几次、
什么时候能再出门；最近看到的和她当时的想法；最近几次出门去了哪、搜了什么、哪里出错；她现在能读到什么。
能在页面上做：让她现在出门一次（可以顺便搜个话题）、加或删想找的、**扫码登 B站**（不用再去网页对话发指令）、
检查登录、退出登录。B站的登录信息不会发到页面上。

## 支持的平台

只用 AstrBot 的通用消息事件，不依赖哪个平台独有的功能。需要的是：私聊里认得出谁是主人（她自己上网的次数、扫码登录都只在主人私聊里）。元数据里列的是满足这些的主流平台：Telegram、QQ（OneBot / aiocqhttp）、Discord、KOOK、飞书、钉钉、企业微信、Slack。**实际测过的只有 Telegram 和 AstrBot 自带的网页对话**，其他平台欢迎反馈。需要 AstrBot 4.24.2 及以上。

## 致谢

思路参考 [DailyHotApi](https://github.com/imsyy/DailyHotApi)（把众多热榜统一成一种格式）、
[astrbot_plugin_proactive_chat](https://github.com/DBJD-CR/astrbot_plugin_proactive_chat)（随机时间、安静时段、用户活跃时等待；只借鉴思路，没有复制代码）和 Letta 的
[sleep-time agents](https://letta.com/blog/sleep-time-compute)（角色自己的活动和聊天分开进行）。
读视频的做法参考了 [dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll) 和 [bilibili-mcp](https://github.com/adoresever/bilibili-mcp)（都是 MIT）：先读字幕，没有字幕就从预览图里截画面。
听视频用 [SenseVoice](https://github.com/FunAudioLLM/SenseVoice)（FunASR 模型许可）和 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)（Apache-2.0）转换好的模型，解码用 [PyAV](https://github.com/PyAV-Org/PyAV)。

## 许可证

[Apache-2.0](LICENSE)
