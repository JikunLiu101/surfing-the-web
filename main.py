"""surfing-the-web: lets an AstrBot persona go online on its own.

A few times a day, when nobody is chatting with it, the persona looks at a few public lists (Bilibili, tech
news, forums...), opens what it finds interesting and thinks something about it. What it saw becomes its own
experience: with the memory-core plugin, episodes in its long-term memory (consolidated, in the diary);
without it, a local log. Read-only, allow-listed sites, no tools while browsing.

Opening something means reading it: a web page through DeepSeek's Fetch API (search through its Search API,
with the DeepSeek key AstrBot already has), a Bilibili video through its subtitles (with a Bilibili account the
owner logs in by scanning a QR code, /surf_bili_login) or, without subtitles, a few frames described by a vision
model.
"""

from __future__ import annotations

import asyncio
import random
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart

from .bili import (LOGIN_HOSTS, QR_DONE, QR_EXPIRED, QR_SCANNED, BiliReader, load_login, login_state,
                   mark_expired, qr_poll, qr_start, save_login, whoami)
from .reading import Reader
from .sources import BUILTIN, PageReader, SafeFetcher, build_searches, build_sources
from .wander import (BufferSink, LogSink, MemoryCoreSink, State, asks_to_go_online, due, go_out, recent_block,
                     strip_blocks)

PLUGIN_NAME = "astrbot_plugin_surfing_the_web"
MEMORY_CORE = "astrbot_plugin_memory_core"
DEFAULT_SEARCHES = ["bilibili_search", "web_search"]
DEFAULT_BLOCKED = ["翻墙", "梯子", "科学上网", "VPN", "政治", "政府", "领导人", "色情", "赌博"]
IDENTITY_DOCS = (("self", "identity"), ("self-image", "self_image"), ("voice", "voice_examples"))


def _now() -> datetime:
    return datetime.now().astimezone()


class SurfingTheWeb(star.Star):
    def __init__(self, context: star.Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.cfg = config or {}
        self.data_dir = Path(star.StarTools.get_data_dir(PLUGIN_NAME))
        self.state_path = self.data_dir / "state.json"
        self.state = State.load(self.state_path)
        self.login_path = self.data_dir / "bilibili_login.json"  # her Bilibili account (never logged, not in git)
        self._warned: set[str] = set()
        self.rng = random.Random()
        self.owner_last_seen: datetime | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    async def initialize(self) -> None:
        if self.cfg.get("enabled", True):
            self._task = asyncio.create_task(self._loop())
        logger.info(f"[surfing] sources: {', '.join(s.key for s in self._sources()) or '(none)'}; "
                    f"memory-core: {'yes' if self._memory() else 'no (local log)'}")

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()

    # ---------------------------------------------------------------- memory-core (optional)

    def _memory(self):
        """The memory-core plugin instance when it is installed, active and has its memory open."""
        try:
            meta = self.context.get_registered_star(MEMORY_CORE)
        except Exception:  # noqa: BLE001
            return None
        plugin = getattr(meta, "star_cls", None) if meta and getattr(meta, "activated", True) else None
        return plugin if getattr(plugin, "core", None) is not None else None

    def _owner_ids(self) -> set[str]:
        own = {str(x).strip() for x in self.cfg.get("owner_ids", []) if str(x).strip()}
        if own:
            return own
        mem = self._memory()
        return {str(x) for x in getattr(mem, "owner_ids", [])} if mem else set()

    def _console_platforms(self) -> set[str]:
        return {str(x).strip() for x in self.cfg.get("owner_command_platforms", ["webchat"]) if str(x).strip()}

    def _console_denied(self, event: AstrMessageEvent) -> bool:
        """Commands only for the owner, in a private chat, on a listed platform (default: the local web chat).
        Anyone else gets no answer at all."""
        ok = (not event.get_group_id() and str(event.get_sender_id()) in self._owner_ids()
              and (event.get_platform_name() or "") in self._console_platforms())
        if ok:
            return False
        logger.warning(f"[surfing] refused {event.get_message_str()!r} from "
                       f"{event.get_platform_name()}:{event.get_sender_id()}")
        event.stop_event()
        return True

    async def _persona(self) -> str:
        mem = self._memory()
        if mem is not None:
            def read():
                docs = [(tag, mem.core.identity(name)) for name, tag in IDENTITY_DOCS]
                return "\n\n".join(f"<{tag}>\n{t.strip()}\n</{tag}>" for tag, t in docs if t and t.strip())
            text = await asyncio.to_thread(read)
            if text:
                return text
        own = (self.cfg.get("persona_prompt") or "").strip()
        if own:
            return own
        try:
            p = await self.context.persona_manager.get_default_persona_v3()
            prompt = p.get("prompt") if isinstance(p, dict) else getattr(p, "prompt", "")
            if prompt:
                return prompt
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[surfing] cannot read the default persona: {exc}")
        return "你是一个喜欢上网冲浪的人。"

    def _sink(self):
        mem = self._memory()
        if mem is not None:
            return MemoryCoreSink(mem.core)
        return LogSink(self.data_dir / "seen.jsonl")

    # ---------------------------------------------------------------- an outing

    def _sources(self):
        return build_sources(list(self.cfg.get("sources", list(BUILTIN))),
                             str(self.cfg.get("dailyhot_url", "") or ""),
                             list(self.cfg.get("dailyhot_routes", [])))

    def _provider_id(self) -> str | None:
        configured = (self.cfg.get("llm_provider_id") or "").strip()
        if configured:
            return configured
        provider = self.context.get_using_provider()
        return provider.meta().id if provider else None

    def _blocked(self) -> tuple[str, ...]:
        return tuple(str(w).strip() for w in self.cfg.get("blocked_keywords", DEFAULT_BLOCKED) if str(w).strip())

    def _owner_private(self, event: AstrMessageEvent) -> bool:
        return not event.get_group_id() and str(event.get_sender_id()) in self._owner_ids()

    def _self_rules(self) -> tuple[int, int]:
        return int(self.cfg.get("self_outings_per_day", 3)), int(self.cfg.get("self_cooldown_minutes", 60))

    def _asked_rules(self) -> tuple[int, float]:
        """Outings the owner asks her for: a ceiling of their own and a short gap, nothing else."""
        return int(self.cfg.get("asked_outings_per_day", 10)), float(self.cfg.get("asked_min_gap_minutes", 5))

    def being_abilities(self, owner: str) -> list[tuple[str, str, str]]:
        """What this plugin lets her do, as (key, name, how it works), for the plugin that tells her what she
        can do (memory-core). Follows the settings, so it stays true."""
        out = []
        if self.cfg.get("enabled", True) and int(self.cfg.get("outings_per_day", 3)) > 0:
            out.append(("surfing-the-web:outings", "自己上网逛逛",
                        f"每天{int(self.cfg.get('start_hour', 10))}点到{int(self.cfg.get('end_hour', 23))}点之间会自己"
                        f"出门{int(self.cfg.get('outings_per_day', 3))}次左右，刷几个常去的站点，看到的会记下来"))
        searches = self._searches()
        if searches:
            where = "和".join(dict.fromkeys(s.site for s in searches))
            out.append(("surfing-the-web:wish", "记下想上网查的",
                        f"和{owner}私聊时用 surf_wish 记下想查的话题，下次出门先去搜（{where}）"))
        per_day, cooldown = self._self_rules()
        if per_day > 0:
            out.append(("surfing-the-web:now", "想去就马上上网",
                        f"和{owner}私聊时用 surf_now 现在就去逛一会儿，一天至多{per_day}次，两次之间要歇一会儿"))
        asked_per_day, _ = self._asked_rules()
        if asked_per_day > 0:
            out.append(("surfing-the-web:asked", f"{owner}让去就马上上网",
                        f"{owner}在私聊里亲口让你去上网时（比如“去上网吧”），用 surf_now 并填 asked=true，马上就去，"
                        f"不占你自己的次数，也不用等休息（这样多出去的一天至多{asked_per_day}次）"))
        out += self._reading_abilities(owner)
        return out

    def _reading_abilities(self, owner: str) -> list[tuple[str, str, str]]:
        """What opening something gets her, and how to get back what does not work. Reading subtitles is its own
        entry: it comes with the Bilibili login and goes when the login expires, so she notices both."""
        reads, fixes = [], []
        if self.cfg.get("read_pages", True):
            if self._deepseek_key():
                reads.append("网页（包括搜到的）能读正文")
            else:
                fixes.append(f"读网页正文要一个 DeepSeek key，现在没有，得{owner}在插件配置里填上")
        bili = self._bili_state()
        if bili != "off":
            if bili == "ok":
                reads.append("B站视频能读字幕（里面说了什么）")
            if self._viewer() is not None:
                reads.append("B站视频没字幕时能看里面几个画面")
            if bili == "none":
                fixes.append(f"读B站字幕要{owner}在电脑的网页对话里发 /surf_bili_login，用手机B站扫码给你登一个账号，"
                             "现在还没登")
            elif bili == "expired":
                fixes.append(f"B站账号登录过期了，读不到字幕；要{owner}在电脑的网页对话里发 /surf_bili_login 重新扫码")
        out = []
        if reads or fixes:
            text = "；".join(reads) if reads else "只看得到标题和简介"
            out.append(("surfing-the-web:read", "上网时点开看内容", text + ("。" + "；".join(fixes) if fixes else "")))
        if bili == "ok":
            out.append(("surfing-the-web:subtitles", "看B站视频里说了什么",
                        f"点开B站视频能读到里面说的话（字幕），用的是{owner}给你登的B站账号；登录大约半年后会过期"))
        return out

    def _bili_state(self) -> str:
        """"off" (not reading videos), "ok", "expired" or "none" (never logged in)."""
        return login_state(self.login_path) if self.cfg.get("read_videos", True) else "off"

    def _searches(self):
        return build_searches(list(self.cfg.get("search_sources", DEFAULT_SEARCHES)), self._deepseek_key())

    def _owner_title(self) -> str:
        return str(getattr(self._memory(), "owner_title", "") or "主人")

    def _trouble_texts(self, owner: str) -> list[str]:
        """What does not work right now, in her words, with whom to ask (for her note and surf_now)."""
        out = []
        if self._bili_state() == "expired":
            out.append(f"B站账号登录过期了：点开视频读不到字幕，只能看画面。请{owner}在电脑的网页对话里发 "
                       "/surf_bili_login 重新扫码就好")
        texts = {
            "deepseek_balance": f"网页搜索、读网页用的 DeepSeek 余额不够了，搜不了也读不了网页正文；要{owner}去充值",
            "deepseek_key": f"网页搜索、读网页用的 DeepSeek key 不能用了；要{owner}检查一下",
            "vision_down": f"看画面用的本机模型没有反应（可能没开），没字幕的视频看不了画面；要{owner}看看电脑上的 Ollama",
        }
        out += [texts[t] for t in self.state.troubles if t in texts]
        return out

    @staticmethod
    def _troubles_of(outing, reader) -> list[str]:
        """Codes for what did not work on this outing (kept until the next outing)."""
        errors = " ".join(outing.errors)
        out = []
        if "api.deepseek.com" in errors and "HTTP 402" in errors:
            out.append("deepseek_balance")
        if "api.deepseek.com" in errors and "HTTP 401" in errors:
            out.append("deepseek_key")
        bili = reader.bili
        if bili is not None and bili.looks and bili.vision_failures >= bili.looks:
            out.append("vision_down")
        return out

    async def outing(self, topic: str = ""):
        """Go out once now, looking up `topic` (or her oldest wish). Returns the Outing, or None when there is
        nothing to do it with."""
        provider_id = self._provider_id()
        sources = self._sources()
        if not provider_id or not sources:
            logger.warning("[surfing] no chat model or no source configured")
            return None
        k = max(1, int(self.cfg.get("sources_per_outing", 3)))
        picked = self.rng.sample(sources, min(k, len(sources)))

        async def complete(system: str, user: str) -> str:
            # llm_generate: no tools, no conversation, not recorded as a chat reply
            resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=user, system_prompt=system)
            return resp.completion_text or ""

        sink = self._sink()
        buffered = BufferSink()  # memory-core writes block: they run in a thread afterwards
        searches = self._searches()
        reader = self._reader()
        async with self._lock:
            from_wishes = not topic
            wish = (topic or self.state.take_wish()) if searches else ""
            outing = await go_out(picked, complete, buffered, persona=await self._persona(), now=_now(),
                                  already_seen=set(self.state.seen_keys),
                                  per_source=int(self.cfg.get("items_per_source", 8)),
                                  max_picks=int(self.cfg.get("max_picks", 3)), blocked=self._blocked(),
                                  wish=wish, searches=searches, per_search=int(self.cfg.get("results_per_search", 6)),
                                  rng=self.rng, reader=reader)
            search_keys = {s.key for s in searches}
            failed = [e for e in outing.errors if e.split(":", 1)[0] in search_keys]
            if wish and from_wishes and not outing.found and len(failed) == len(searches):
                self.state.wishes.append({"topic": wish, "reason": "上次没搜成", "at": _now().isoformat()})
            for row in buffered.rows:
                await asyncio.to_thread(sink.record, *row)
            self.state.remember(outing)
            self.state.rested_after(_now(), self._self_rules()[1], self._jitter(), self.rng)
            await asyncio.to_thread(self.state.save, self.state_path)
        logger.info(f"[surfing] outing ({', '.join(outing.sources)}"
                    + (f"; looked up {outing.wish!r}: {outing.found} found" if outing.wish else "")
                    + f"): {outing.shown} shown, {len(outing.seen)} opened"
                    + (f", errors: {outing.errors}" if outing.errors else ""))
        if reader.bili is not None and reader.bili.login_expired:
            await asyncio.to_thread(mark_expired, self.login_path)
            logger.warning("[surfing] the Bilibili login has expired: send /surf_bili_login again")
        self.state.troubles = self._troubles_of(outing, reader)
        if self.state.troubles:
            logger.warning(f"[surfing] troubles on this outing: {', '.join(self.state.troubles)}")
        await asyncio.to_thread(self.state.save, self.state_path)
        return outing

    # ---------------------------------------------------------------- reading what she opens

    def _warn_once(self, key: str, text: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(text)

    def _deepseek_key(self) -> str:
        """For web search and reading pages: the plugin's own key, else the key of AstrBot's DeepSeek provider.
        Never logged."""
        own = str(self.cfg.get("deepseek_api_key") or "").strip()
        if own:
            return own
        try:
            sources = self.context.get_config().get("provider_sources", []) or []
        except Exception:  # noqa: BLE001
            sources = []
        for src in sources:
            if "api.deepseek.com" in str(src.get("api_base") or "") and src.get("enable", True):
                keys = src.get("key") or []
                keys = [keys] if isinstance(keys, str) else keys
                for k in keys:
                    if str(k).strip():
                        return str(k).strip()
        return ""

    def _viewer(self):
        """The vision model that looks at video frames: vision_provider_id, else the first provider that takes
        images. None: no frames."""
        if not self.cfg.get("video_frames", True):
            return None
        pid = str(self.cfg.get("vision_provider_id") or "").strip()
        provider = self.context.get_provider_by_id(pid) if pid else None
        if provider is None and not pid:
            for p in self.context.get_all_providers():
                if "image" in ((getattr(p, "provider_config", None) or {}).get("modalities") or []):
                    provider = p
                    break
        if provider is None:
            self._warn_once("no-vision", "[surfing] no vision model: videos without subtitles are not looked at")
            return None
        timeout = float(self.cfg.get("vision_timeout", 45))

        async def see(jpeg: bytes, prompt: str) -> str:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fh:
                fh.write(jpeg)
                path = fh.name
            try:
                resp = await asyncio.wait_for(provider.text_chat(prompt=prompt, image_urls=[path]), timeout)
                return resp.completion_text or ""
            finally:
                Path(path).unlink(missing_ok=True)

        return see

    def _reader(self) -> Reader:
        key = self._deepseek_key()
        pages = PageReader(key) if key and self.cfg.get("read_pages", True) else None
        bili = None
        if self.cfg.get("read_videos", True):
            bili = BiliReader(load_login(self.login_path), self._viewer(),
                              max_looks=int(self.cfg.get("frames_videos_per_outing", 2)))
        return Reader(bili, pages)

    async def _roll_day(self, now: datetime) -> None:
        """A new day: plan today's outings, and her own outings start again from zero."""
        if self.state.day != now.date().isoformat():
            self.state.new_day(now.date(), int(self.cfg.get("outings_per_day", 3)) if self.cfg.get("enabled", True)
                               else 0, int(self.cfg.get("start_hour", 10)), int(self.cfg.get("end_hour", 23)),
                               self.rng, now)
            await asyncio.to_thread(self.state.save, self.state_path)
            times = [datetime.fromisoformat(t).strftime("%H:%M") for t in self.state.plan]
            logger.info(f"[surfing] {self.state.day}: outings planned at {', '.join(times) or '(none)'}"
                        + (f" ({self.state.done} already past)" if self.state.done else ""))

    def _resting(self, now: datetime) -> bool:
        """Right after an outing (of any kind) the next scheduled one waits."""
        ready = self.state.resting_until(self._self_rules()[1])
        return ready is not None and now < ready

    def _jitter(self) -> float:
        return max(0.0, min(0.9, float(self.cfg.get("timing_jitter", 0.2))))

    async def _loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                now = _now()
                await self._roll_day(now)
                if not self._resting(now) and due(now, self.state.plan, self.state.done, self.owner_last_seen,
                                                  int(self.cfg.get("owner_idle_minutes", 30))):
                    self.state.done += 1  # counted even if it fails: no retry storm
                    await asyncio.to_thread(self.state.save, self.state_path)
                    logger.info(f"[surfing] scheduled outing {self.state.done}/{len(self.state.plan)} today")
                    await self.outing()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[surfing] outing failed: {exc}")
            await asyncio.sleep(120)

    # ---------------------------------------------------------------- hooks

    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def on_message(self, event: AstrMessageEvent):
        """Notice when the owner is around (she does not go out in the middle of a chat). Never replies."""
        if str(event.get_sender_id()) in self._owner_ids():
            self.owner_last_seen = _now()

    @filter.on_llm_request()
    async def inject(self, event: AstrMessageEvent, req: ProviderRequest):
        """Tell her she goes online on her own, and what she saw lately. It goes with the user's message (it
        changes after every outing; the system prompt stays the same, so the provider's prompt cache covers it)
        and is taken out of the earlier messages in the history."""
        try:
            strip_blocks(req.contexts, ("surfing_the_web",))
        except Exception as exc:  # noqa: BLE001 - never block a reply
            logger.warning(f"[surfing-the-web] history cleanup failed: {exc}")
        if not self.cfg.get("inject_recent", True):
            return
        owner_private = self._owner_private(event)
        now = _now()
        self_left, self_ready, asked_left = None, "", None
        per_day, cooldown = self._self_rules()
        if owner_private:
            asked_left = max(0, self._asked_rules()[0] - self.state.asked_used)
        if owner_private and per_day > 0:
            self_left = max(0, per_day - self.state.self_used)
            ready = self.state.resting_until(cooldown)
            if ready is not None:
                self_ready = ready.strftime("%H:%M") if ready > now else ""
        block = recent_block(self.state.recent, now, hours=int(self.cfg.get("recent_hours", 24)),
                             with_thoughts=owner_private, wishes=self.state.wishes,
                             can_wish=owner_private and bool(self.cfg.get("search_sources", DEFAULT_SEARCHES)),
                             self_left=self_left, self_ready=self_ready,
                             troubles=self._trouble_texts(self._owner_title()) if owner_private else [],
                             asked_left=asked_left)
        req.extra_user_content_parts.append(TextPart(text=block))

    @filter.llm_tool(name="surf_now")
    async def tool_surf_now(self, event: AstrMessageEvent, topic: str = "", reason: str = "", asked: bool = False):
        """Go online right now, for a little while, and come back with what you saw. Optionally look a topic up
        (public topic words only, never anyone's personal details). On your own you can only do this a few times a
        day, with a rest in between: go when you really feel like it. When the person you are talking to has just
        asked you to go online (e.g. 去上网吧), set asked=true: then those limits do not apply.

        Args:
            topic(string): optional, a few words to search, e.g. 明日方舟 新干员
            reason(string): why you want to go now
            asked(boolean): true only if the person's last message asked you to go online
        """
        if not self._owner_private(event):
            logger.info("[surfing] surf_now refused outside the owner's private chat")
            return "现在不能自己出门上网（只有和最亲近的人私聊时才可以）"
        now = _now()
        await self._roll_day(now)
        asked = asked is True or str(asked).strip().lower() in ("true", "1", "yes")
        if asked and not asks_to_go_online(event.get_message_str()):
            logger.info("[surfing] surf_now: she said she was asked, but the message does not ask her to go online")
            return "他这句话里没让你去上网，不能算他让你去的。想自己去就别填 asked（自己去有次数限制）"
        ok, why = (self.state.may_go_when_asked(now, *self._asked_rules()) if asked
                   else self.state.may_go_myself(now, *self._self_rules()))
        if not ok:
            return why
        if topic.strip():
            fine, msg = State().add_wish(topic, "", now, self._blocked())  # same checks as a wish
            if not fine:
                return msg
            topic = msg
        if asked:  # counted before going: a failure is not a free retry
            self.state.asked_used += 1
            self.state.last_asked_at = now.isoformat()
        else:
            self.state.self_used += 1
        await asyncio.to_thread(self.state.save, self.state_path)
        if asked:
            logger.info(f"[surfing] she goes online because she was asked ({self.state.asked_used} today, not"
                        f" counted as her own): {topic!r} {reason!r}")
        else:
            logger.info(f"[surfing] she goes online herself ({self.state.self_used} today): {topic!r} {reason!r}")
        try:
            outing = await asyncio.wait_for(self.outing(topic), timeout=100)
        except asyncio.TimeoutError:
            return "网太慢了，这次没逛成"
        if outing is None:
            return "现在上不了网"
        left = max(0, self._self_rules()[0] - self.state.self_used)
        count = "这次是对方让你去的，不算你自己的次数；" if asked else ""
        head = (f"搜了「{outing.wish}」，找到 {outing.found} 条；" if outing.wish else "") + f"一共刷了 {outing.shown} 条"
        trouble = self._trouble_texts(self._owner_title())
        tail = ("\n上网有点不顺：" + "；".join(trouble)) if trouble else ""
        if not outing.seen:
            return f"逛回来了：{head}，没有想点开的。{count}今天还能自己去 {left} 次{tail}"
        lines = [f"- 在{s.site}看到《{s.title}》" + (f"，你当时想：{s.thought}" if s.thought else "") for s in outing.seen]
        return (f"逛回来了：{head}，点开了 {len(outing.seen)} 条：\n" + "\n".join(lines)
                + f"\n（这些已经记进你的记忆。{count}今天还能自己去 {left} 次）{tail}")

    @filter.llm_tool(name="surf_wish")
    async def tool_wish(self, event: AstrMessageEvent, topic: str, reason: str = ""):
        """Note something you want to look up online; next time you go out on your own you search it first
        (Bilibili and web search). Use public topic words only, e.g. a game, a show or a piece of news; never anyone's
        name, address, workplace or other personal details.

        Args:
            topic(string): a few words to search, e.g. 明日方舟 新干员
            reason(string): why you want to know
        """
        if not self._owner_private(event):
            # only the owner shapes what she goes looking for
            logger.info(f"[surfing] surf_wish refused outside the owner's private chat: {topic!r}")
            return "想上网找什么，只能私下和最亲近的人商量，现在不能记"
        ok, msg = self.state.add_wish(topic, reason, _now(), self._blocked())
        if not ok:
            return msg
        await asyncio.to_thread(self.state.save, self.state_path)
        return f"记下了：下次出门先去搜「{msg}」（现在想找的有 {len(self.state.wishes)} 个）"

    # ---------------------------------------------------------------- owner commands

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("surf")
    async def cmd_surf(self, event: AstrMessageEvent):
        """让她现在出门上网逛一次：/surf，或 /surf 话题（顺便去搜这个）"""
        if self._console_denied(event):
            return
        parts = (event.get_message_str() or "").strip().split(maxsplit=1)
        topic = parts[1].strip() if len(parts) > 1 else ""
        if topic:
            ok, msg = State().add_wish(topic, "", _now(), self._blocked())  # same checks as her own wishes
            if not ok:
                yield event.plain_result(msg)
                return
            topic = msg
        try:
            outing = await self.outing(topic)
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"出门失败：{exc}")
            return
        if outing is None:
            yield event.plain_result("没有可用的聊天模型或站点")
            return
        head = f"去了 {'、'.join(outing.sources)}" + (f"，搜「{outing.wish}」找到 {outing.found} 条" if outing.wish else "")
        tail = f"\n（出错：{'; '.join(outing.errors)}）" if outing.errors else ""
        if not outing.seen:
            yield event.plain_result(f"{head}，刷了 {outing.shown} 条，没有想点开的{tail}")
            return
        lines = [f"· {s.site}｜{s.title}\n  {s.thought or '（没想什么）'}" for s in outing.seen]
        yield event.plain_result(f"{head}，刷了 {outing.shown} 条，点开了 {len(outing.seen)} 条：\n"
                                 + "\n".join(lines) + tail)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("surf_log")
    async def cmd_log(self, event: AstrMessageEvent):
        """她最近上网看了什么，以及今天的出门计划"""
        if self._console_denied(event):
            return
        plan = ", ".join(("✓" if i < self.state.done else "") + datetime.fromisoformat(t).strftime("%H:%M")
                         for i, t in enumerate(self.state.plan)) or "（没有）"
        recent = [f"· {datetime.fromisoformat(r['at']).strftime('%m-%d %H:%M')} {r['site']}｜{r['title']}"
                  + (f"\n  {r['thought']}" if r.get("thought") else "") for r in self.state.recent[-8:]]
        wishes = "、".join(f"「{w['topic']}」" for w in self.state.wishes) or "（没有）"
        per_day, _ = self._self_rules()
        plan += f"\n她今天自己出门：{self.state.self_used}/{per_day} 次"
        last = self.state.outings[-1] if self.state.outings else None
        last_line = (f"\n上次出门：{', '.join(last.get('sources', []))}"
                     + (f"，搜「{last['wish']}」{last.get('found', 0)} 条" if last.get("wish") else "")) if last else ""
        yield event.plain_result(f"今天的计划：{plan}\n想找的：{wishes}{last_line}\n\n最近看过：\n"
                                 + ("\n".join(recent) or "（还没有）"))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("surf_bili_login")
    async def cmd_bili_login(self, event: AstrMessageEvent):
        """给她登录一个B站账号（用来读字幕，建议用小号）：发出二维码，用手机B站扫码确认"""
        if self._console_denied(event):
            return
        png = self.data_dir / "bilibili_qr.png"
        try:
            import qrcode  # noqa: PLC0415 - comes with AstrBot

            async with SafeFetcher(set(LOGIN_HOSTS)) as f:
                url, key = await qr_start(f)
            png.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(lambda: qrcode.make(url).save(png))
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"生成二维码失败：{exc}")
            return
        yield event.image_result(str(png))
        yield event.plain_result("用手机B站扫这个码，在手机上点确认（建议用小号；她只用它读字幕，不发东西）。"
                                 "三分钟内有效。")
        try:
            said_scanned = False
            async with SafeFetcher(set(LOGIN_HOSTS)) as f:
                for _ in range(90):
                    await asyncio.sleep(2)
                    code, cookies, seen = await qr_poll(f, key)
                    if code == QR_DONE and not cookies:  # the key is used up: polling on would only say "expired"
                        logger.warning(f"[surfing] bilibili confirmed the scan but sent no login; got {seen}")
                        yield event.plain_result("手机上确认成功了，但B站没把登录信息发过来，所以没登上。"
                                                 "把 AstrBot 日志里带 bilibili 的那行发给开发者看看。")
                        return
                    if code == QR_DONE:
                        await asyncio.to_thread(save_login, self.login_path, cookies)
                        name = await whoami(f, cookies)
                        logger.info("[surfing] Bilibili login saved" + (" (the login works)" if name else
                                                                         f" (but nav does not see it logged in; got {seen})"))
                        yield event.plain_result(f"登录好了{'：' + name if name else ''}。以后她点开B站视频会读字幕。"
                                                 + ("" if name else "（不过查不到账号名，读字幕时如果不行再扫一次）"))
                        return
                    if code == QR_SCANNED and not said_scanned:
                        said_scanned = True
                        yield event.plain_result("扫到了，在手机上点确认")
                    if code == QR_EXPIRED:
                        yield event.plain_result("二维码过期了，再发一次 /surf_bili_login")
                        return
            yield event.plain_result("等太久了，没登录上，再发一次 /surf_bili_login")
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"登录没成功：{exc}")
        finally:
            png.unlink(missing_ok=True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("surf_bili_status")
    async def cmd_bili_status(self, event: AstrMessageEvent):
        """她的B站账号还登录着吗，网页搜索和看画面能不能用"""
        if self._console_denied(event):
            return
        login = load_login(self.login_path)
        name = ""
        if login:
            try:
                async with SafeFetcher(set(LOGIN_HOSTS)) as f:
                    name = await whoami(f, login)
            except Exception as exc:  # noqa: BLE001
                name = f"（查不了：{exc}）"
        if login_state(self.login_path) == "expired":
            bili = "B站：登录过期了，再发 /surf_bili_login"
        elif not login:
            bili = "B站：没登录（读不了字幕），发 /surf_bili_login"
        else:
            bili = f"B站：登录着 {name}" if name else "B站：登录过期了，再发 /surf_bili_login"
        web = "网页搜索和读网页：可以（DeepSeek）" if self._deepseek_key() else "网页搜索和读网页：没有 DeepSeek key"
        frames = "没字幕时看画面：" + ("可以" if self._viewer() else "没有能看图的模型")
        yield event.plain_result("\n".join((bili, web, frames)))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("surf_bili_logout")
    async def cmd_bili_logout(self, event: AstrMessageEvent):
        """删掉她保存的B站登录"""
        if self._console_denied(event):
            return
        self.login_path.unlink(missing_ok=True)
        yield event.plain_result("删掉了。之后没字幕的视频她只能看画面。")
