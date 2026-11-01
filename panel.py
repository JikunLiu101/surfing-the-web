"""The owner's dashboard for surfing-the-web: a plugin view (pages/surfing/) and the Web API behind it.

AstrBot serves plugin Web APIs only to a logged-in dashboard user (or an API key with the plugin scope). What it can
do is what the owner commands do (/surf, /surf_log, /surf_bili_login, /surf_bili_status, /surf_bili_logout), plus
adding and removing what she wants to look up. The Bilibili login itself is never sent to the page.
"""

from __future__ import annotations

import asyncio
import base64
import io
import inspect
from datetime import datetime
from typing import Any

PREFIX = "/astrbot_plugin_surfing_the_web/page"


def _ok(data: Any) -> dict:
    return {"status": "ok", "data": data}


def _error(message: str) -> dict:
    return {"status": "error", "message": message}


def _now() -> datetime:
    return datetime.now().astimezone()


def outing_summary(outing) -> dict:
    """What one outing did, for the page."""
    return {"at": outing.at, "sources": list(outing.sources), "wish": outing.wish, "found": outing.found,
            "shown": outing.shown, "seen": [{"site": s.site, "title": s.title, "url": s.url, "thought": s.thought}
                                            for s in outing.seen],
            "errors": list(outing.errors[:5])}


class Panel:
    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self.going: asyncio.Task | None = None
        self.last_go: dict = {}
        self.qr: dict = {"state": "idle"}
        self._qr_task: asyncio.Task | None = None

    def register(self) -> bool:
        register = getattr(self.plugin.context, "register_web_api", None)
        if not callable(register):
            return False
        routes = [("overview", self.overview, ["GET"]), ("go", self.go, ["POST"]),
                  ("wish_add", self.wish_add, ["POST"]), ("wish_remove", self.wish_remove, ["POST"]),
                  ("bili_qr", self.bili_qr, ["POST"]), ("bili_qr_status", self.bili_qr_status, ["GET"]),
                  ("bili_check", self.bili_check, ["POST"]), ("bili_logout", self.bili_logout, ["POST"])]
        for name, handler, methods in routes:
            register(f"{PREFIX}/{name}", self._guarded(handler), methods, f"surfing-the-web dashboard: {name}")
        return True

    @staticmethod
    def _guarded(handler):
        async def run(**_):
            try:
                return _ok(await handler())
            except ValueError as exc:
                return _error(str(exc))
            except Exception as exc:  # noqa: BLE001 - the page shows the message instead of a blank screen
                from astrbot.api import logger  # noqa: PLC0415

                logger.warning(f"[surfing] dashboard {handler.__name__} failed: {exc}")
                return _error(f"{type(exc).__name__}: {exc}")

        run.__name__ = handler.__name__
        return run

    @staticmethod
    def _request():
        from astrbot.api.web import request  # noqa: PLC0415 - only exists inside a Web API handler

        return request

    async def _body(self) -> dict:
        body = self._request().json()
        if inspect.isawaitable(body):
            body = await body
        return body if isinstance(body, dict) else {}

    # ------------------------------------------------------------------------------------- reading

    async def overview(self) -> dict:
        p = self.plugin
        st = p.state
        now = _now()
        per_day, cooldown = p._self_rules()
        asked_per_day, _ = p._asked_rules()
        ready = st.resting_until(cooldown)
        plan = [{"t": t, "done": i < st.done} for i, t in enumerate(st.plan)] if st.day == now.date().isoformat() else []
        return {
            "enabled": bool(p.cfg.get("enabled", True)),
            "owner": p._owner_title(),
            "memory_core": p._memory() is not None,
            "plan": plan,
            "self_used": st.self_used, "self_per_day": per_day,
            "asked_used": st.asked_used, "asked_per_day": asked_per_day,
            "resting_until": ready.isoformat() if ready and ready > now else "",
            "recent": list(reversed(st.recent[-20:])),
            "outings": list(reversed(st.outings[-10:])),
            "wishes": list(st.wishes),
            "sources": [s.key for s in p._sources()],
            "searches": [s.site for s in p._searches()],
            "can": {"bili": p._bili_state(), "web": bool(p._deepseek_key()), "frames": p._viewer() is not None,
                    "chat_model": bool(p._provider_id())},
            "troubles": p._trouble_texts(p._owner_title()),
            "going": bool(self.going and not self.going.done()),
            "last_go": self.last_go,
        }

    # ------------------------------------------------------------------------------------- going out

    async def go(self) -> dict:
        """Send her out now (in the background: an outing takes a minute or two). Like /surf."""
        if self.going and not self.going.done():
            raise ValueError("她已经在外面了，等她回来")
        body = await self._body()
        topic = str(body.get("topic") or "").strip()
        p = self.plugin
        if topic:
            from .wander import State  # noqa: PLC0415

            ok, msg = State().add_wish(topic, "", _now(), p._blocked())  # the same checks as her own wishes
            if not ok:
                raise ValueError(msg)
            topic = msg
        if not p._provider_id() or not p._sources():
            raise ValueError("没有可用的聊天模型或站点")
        self.last_go = {"state": "going", "topic": topic, "started": _now().isoformat()}
        self.going = asyncio.create_task(self._go(topic))
        return {"started": True}

    async def _go(self, topic: str) -> None:
        try:
            outing = await self.plugin.outing(topic)
            self.last_go = ({"state": "done", "topic": topic, "outing": outing_summary(outing)} if outing is not None
                            else {"state": "failed", "topic": topic, "error": "没有可用的聊天模型或站点"})
        except Exception as exc:  # noqa: BLE001
            self.last_go = {"state": "failed", "topic": topic, "error": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------------------------- wishes

    async def wish_add(self) -> dict:
        body = await self._body()
        p = self.plugin
        ok, msg = p.state.add_wish(str(body.get("topic") or ""), str(body.get("reason") or ""), _now(), p._blocked())
        if not ok:
            raise ValueError(msg)
        await asyncio.to_thread(p.state.save, p.state_path)
        return {"wishes": list(p.state.wishes)}

    async def wish_remove(self) -> dict:
        body = await self._body()
        topic = str(body.get("topic") or "").strip().lower()
        p = self.plugin
        before = len(p.state.wishes)
        p.state.wishes = [w for w in p.state.wishes if w["topic"].lower() != topic]
        if len(p.state.wishes) == before:
            raise ValueError("没有这个想找的")
        await asyncio.to_thread(p.state.save, p.state_path)
        return {"wishes": list(p.state.wishes)}

    # ------------------------------------------------------------------------------------- Bilibili login

    async def bili_qr(self) -> dict:
        """A QR code to scan with the Bilibili app; the page then asks bili_qr_status. Like /surf_bili_login."""
        from .bili import LOGIN_HOSTS, qr_start  # noqa: PLC0415
        from .sources import SafeFetcher  # noqa: PLC0415

        if self._qr_task and not self._qr_task.done():
            self._qr_task.cancel()
        import qrcode  # noqa: PLC0415 - comes with AstrBot

        async with SafeFetcher(set(LOGIN_HOSTS)) as f:
            url, key = await qr_start(f)

        def png() -> str:
            buf = io.BytesIO()
            qrcode.make(url).save(buf)
            return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        image = await asyncio.to_thread(png)
        self.qr = {"state": "waiting", "started": _now().isoformat()}
        self._qr_task = asyncio.create_task(self._poll(key))
        return {"image": image, **self.qr}

    async def _poll(self, key: str) -> None:
        from astrbot.api import logger  # noqa: PLC0415

        from .bili import LOGIN_HOSTS, QR_DONE, QR_EXPIRED, QR_SCANNED, qr_poll, save_login, whoami  # noqa: PLC0415
        from .sources import SafeFetcher  # noqa: PLC0415

        p = self.plugin
        try:
            async with SafeFetcher(set(LOGIN_HOSTS)) as f:
                for _ in range(90):
                    await asyncio.sleep(2)
                    code, cookies, seen = await qr_poll(f, key)
                    if code == QR_DONE and not cookies:
                        logger.warning(f"[surfing] bilibili confirmed the scan but sent no login; got {seen}")
                        self.qr = {"state": "failed", "message": "手机上确认了，但B站没把登录信息发过来。看看 AstrBot 日志里带 bilibili 的那行"}
                        return
                    if code == QR_DONE:
                        await asyncio.to_thread(save_login, p.login_path, cookies)
                        name = await whoami(f, cookies)
                        logger.info("[surfing] Bilibili login saved from the dashboard"
                                    + (" (the login works)" if name else f" (nav does not see it; got {seen})"))
                        self.qr = {"state": "done", "name": name or ""}
                        return
                    if code == QR_SCANNED and self.qr.get("state") != "scanned":
                        self.qr = {"state": "scanned"}
                    if code == QR_EXPIRED:
                        self.qr = {"state": "expired"}
                        return
            self.qr = {"state": "expired"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.qr = {"state": "failed", "message": f"{type(exc).__name__}: {exc}"}

    async def bili_qr_status(self) -> dict:
        return dict(self.qr)

    async def bili_check(self) -> dict:
        """Does Bilibili still see her logged in (asks Bilibili once). Like /surf_bili_status."""
        from .bili import LOGIN_HOSTS, load_login, login_state, whoami  # noqa: PLC0415
        from .sources import SafeFetcher  # noqa: PLC0415

        p = self.plugin
        login = load_login(p.login_path)
        if not login:
            return {"state": login_state(p.login_path), "name": ""}
        async with SafeFetcher(set(LOGIN_HOSTS)) as f:
            name = await whoami(f, login)
        return {"state": "ok" if name else "expired", "name": name or ""}

    async def bili_logout(self) -> dict:
        self.plugin.login_path.unlink(missing_ok=True)
        self.qr = {"state": "idle"}
        return {"state": self.plugin._bili_state()}
