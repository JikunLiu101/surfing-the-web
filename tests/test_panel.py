"""The dashboard's Web API on a stand-in plugin: what it shows, adding and removing wishes, the checks on them."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

from surfing_the_web import panel as panel_mod
from surfing_the_web.wander import Seen, State


class FakeContext:
    def __init__(self):
        self.routes = []

    def register_web_api(self, route, handler, methods, desc):
        self.routes.append((route, methods))


class FakePlugin:
    def __init__(self, tmp_path):
        now = datetime.now().astimezone()
        self.cfg = {"enabled": True}
        self.context = FakeContext()
        self.state = State(day=now.date().isoformat(), plan=[now.isoformat(), (now + timedelta(hours=3)).isoformat()],
                           done=1, recent=[{"at": now.isoformat(), "site": "B站", "title": "罗小黑", "url": "u",
                                            "thought": "可爱"}])
        self.state_path = tmp_path / "state.json"
        self.login_path = tmp_path / "login.json"

    def _self_rules(self): return 3, 60
    def _asked_rules(self): return 10, 5
    def _owner_title(self): return "主人"
    def _memory(self): return None
    def _sources(self): return [NS(key="ithome")]
    def _searches(self): return []
    def _bili_state(self): return "none"
    def _deepseek_key(self): return ""
    def _viewer(self): return None
    def _provider_id(self): return None
    def _blocked(self): return ("翻墙",)
    def _trouble_texts(self, owner): return []


def call(p, handler, body=None):
    p._request = lambda: NS(json=lambda: body or {}, query={})
    return asyncio.run(p._guarded(handler)())


def test_routes_and_overview(tmp_path):
    plugin = FakePlugin(tmp_path)
    p = panel_mod.Panel(plugin)
    assert p.register()
    assert {r for r, _ in plugin.context.routes} >= {f"{panel_mod.PREFIX}/overview", f"{panel_mod.PREFIX}/go",
                                                     f"{panel_mod.PREFIX}/bili_qr"}
    out = call(p, p.overview)
    assert out["status"] == "ok"
    d = out["data"]
    assert [x["done"] for x in d["plan"]] == [True, False] and d["recent"][0]["thought"] == "可爱"
    assert d["can"] == {"bili": "none", "web": False, "frames": False, "chat_model": False} and not d["going"]


def test_wishes_are_checked_and_saved(tmp_path):
    plugin = FakePlugin(tmp_path)
    p = panel_mod.Panel(plugin)
    assert call(p, p.wish_add, {"topic": "明日方舟 新干员"})["data"]["wishes"][0]["topic"] == "明日方舟 新干员"
    assert call(p, p.wish_add, {"topic": "翻墙 教程"}) == {"status": "error", "message": "这个不能去搜"}
    assert call(p, p.wish_add, {"topic": "call 13800138000"})["status"] == "error"
    assert State.load(plugin.state_path).wishes[0]["topic"] == "明日方舟 新干员"
    assert call(p, p.wish_remove, {"topic": "明日方舟 新干员"})["data"]["wishes"] == []
    assert call(p, p.wish_remove, {"topic": "没有的"})["status"] == "error"


def test_go_needs_a_chat_model(tmp_path):
    p = panel_mod.Panel(FakePlugin(tmp_path))
    assert call(p, p.go, {}) == {"status": "error", "message": "没有可用的聊天模型或站点"}


def test_outing_summary():
    o = NS(at="t", sources=["ithome"], wish="", found=0, shown=9,
           seen=[Seen(at="t", site="IT之家", title="新显卡", url="u", thought="贵")], errors=["e"] * 9)
    s = panel_mod.outing_summary(o)
    assert s["seen"] == [{"site": "IT之家", "title": "新显卡", "url": "u", "thought": "贵"}] and len(s["errors"]) == 5

