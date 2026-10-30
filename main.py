"""surfing-the-web: lets an AstrBot persona go online on its own.

A few times a day, when nobody is chatting with it, the persona looks at a few public lists (Bilibili, tech
news, forums...), opens what it finds interesting and thinks something about it. What it saw becomes its own
experience: with the memory-core plugin, episodes in its long-term memory (consolidated, in the diary);
without it, a local log. Read-only, allow-listed sites, no login, no cookies, no tools while browsing.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest

from .sources import BUILTIN, build_sources
from .wander import BufferSink, LogSink, MemoryCoreSink, State, due, go_out, recent_block

PLUGIN_NAME = "astrbot_plugin_surfing_the_web"
MEMORY_CORE = "astrbot_plugin_memory_core"
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

    async def outing(self):
        """Go out once now. Returns the Outing, or None when there is nothing to do it with."""
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
        blocked = tuple(str(w).strip() for w in self.cfg.get("blocked_keywords", DEFAULT_BLOCKED) if str(w).strip())
        async with self._lock:
            outing = await go_out(picked, complete, buffered, persona=await self._persona(), now=_now(),
                                  already_seen=set(self.state.seen_keys),
                                  per_source=int(self.cfg.get("items_per_source", 8)),
                                  max_picks=int(self.cfg.get("max_picks", 3)), blocked=blocked, rng=self.rng)
            for row in buffered.rows:
                await asyncio.to_thread(sink.record, *row)
            self.state.remember(outing)
            await asyncio.to_thread(self.state.save, self.state_path)
        logger.info(f"[surfing] outing: {outing.shown} shown, {len(outing.seen)} opened"
                    + (f", errors: {outing.errors}" if outing.errors else ""))
        return outing

    async def _loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                now = _now()
                if self.state.day != now.date().isoformat():
                    self.state.new_day(now.date(), int(self.cfg.get("outings_per_day", 3)),
                                       int(self.cfg.get("start_hour", 10)), int(self.cfg.get("end_hour", 23)),
                                       self.rng, now)
                    await asyncio.to_thread(self.state.save, self.state_path)
                if due(now, self.state.plan, self.state.done, self.owner_last_seen,
                       int(self.cfg.get("owner_idle_minutes", 30))):
                    self.state.done += 1  # counted even if it fails: no retry storm
                    await asyncio.to_thread(self.state.save, self.state_path)
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
        """Tell her she goes online on her own, and what she saw lately."""
        if not self.cfg.get("inject_recent", True):
            return
        owner_private = not event.get_group_id() and str(event.get_sender_id()) in self._owner_ids()
        block = recent_block(self.state.recent, _now(), hours=int(self.cfg.get("recent_hours", 24)),
                             with_thoughts=owner_private)
        req.system_prompt = f"{(req.system_prompt or '').rstrip()}\n\n{block}\n"

    # ---------------------------------------------------------------- owner commands

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("surf")
    async def cmd_surf(self, event: AstrMessageEvent):
        """让她现在出门上网逛一次"""
        if self._console_denied(event):
            return
        try:
            outing = await self.outing()
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"出门失败：{exc}")
            return
        if outing is None:
            yield event.plain_result("没有可用的聊天模型或站点")
            return
        if not outing.seen:
            tail = f"（出错：{'; '.join(outing.errors)}）" if outing.errors else ""
            yield event.plain_result(f"刷了 {outing.shown} 条，没有想点开的{tail}")
            return
        lines = [f"· {s.site}｜{s.title}\n  {s.thought or '（没想什么）'}" for s in outing.seen]
        yield event.plain_result(f"刷了 {outing.shown} 条，点开了 {len(outing.seen)} 条：\n" + "\n".join(lines))

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
        yield event.plain_result(f"今天的计划：{plan}\n\n最近看过：\n" + ("\n".join(recent) or "（还没有）"))
