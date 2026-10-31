import asyncio
import json
from datetime import datetime

import httpx

from surfing_the_web.sources import (BilibiliSearch, DeepSeekSearch, RssSource, page_text, parse_bilibili_search,
                                     parse_deepseek_search)
from surfing_the_web.wander import BufferSink, State, go_out, looks_personal, normalize_topic, recent_block

NOW = datetime(2026, 10, 30, 15, 0).astimezone()

BILI = {"code": 0, "data": {"result": [
    {"type": "video", "bvid": "BV16A", "title": "【<em class=\"keyword\">新干员</em>】冻河 技能演示", "author": "某UP",
     "play": 4806, "pubdate": 1793333835, "description": "六星辅助"},
    {"type": "ketang", "bvid": "BV2", "title": "课程"},
]}}

DEEPSEEK = {"results": [
    {"title": "明日方舟 新闻", "url": "https://ak.hypergryph.com/news/1", "site_name": "明日方舟官网",
     "date": "2026-10-29T16:00:00+00:00", "summary": "新干员冻河上线", "snippet": "冻河"},
    {"title": "", "url": "https://empty.example/"},
    {"title": "不是网页", "url": "ftp://x.example/"},
]}

FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>显卡降价了</title><link>https://www.ithome.com/0/1.htm</link><description>x</description></item>
</channel></rss>"""

asked = []


def handler(req):
    asked.append(req)
    if req.url.host == "api.bilibili.com":
        return httpx.Response(200, json=BILI)
    if req.url.host == "api.deepseek.com":
        return httpx.Response(200, json=DEEPSEEK)
    if req.url.host == "www.ithome.com":
        return httpx.Response(200, text=FEED)
    return httpx.Response(404)


class FakeModel:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    async def __call__(self, system, user):
        self.calls.append((system, user))
        a = self.answers.pop(0)
        return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)


def test_parsers():
    (v,) = parse_bilibili_search(BILI)
    assert v.title == "【新干员】冻河 技能演示" and v.url == "https://www.bilibili.com/video/BV16A"
    assert "UP主 某UP" in v.summary and "发布 2026-" in v.summary and v.body == "六星辅助"
    (d,) = parse_deepseek_search(DEEPSEEK)
    assert d.url == "https://ak.hypergryph.com/news/1" and d.title == "明日方舟 新闻" and d.site == "网页"
    assert d.summary == "明日方舟官网 · 发布 2026-10-29" and d.body == "新干员冻河上线" and d.meta["read_page"]
    assert page_text("# 标题\n\n\n![图](https://x/a.png)看[这里](https://x/b)\n") == "# 标题\n看这里"


def test_outing_looks_up_the_wish_first():
    asked.clear()
    model = FakeModel({"picks": [1]}, {"thoughts": [{"n": 1, "text": "冻河好帅"}]})
    sink = BufferSink()
    outing = asyncio.run(go_out([RssSource("ithome", "IT之家", "https://www.ithome.com/rss/")], model, sink,
                                persona="我是小星", now=NOW, already_seen=set(), wish="明日方舟 新干员",
                                searches=[BilibiliSearch(), DeepSeekSearch("sk-test")], transport=httpx.MockTransport(handler)))
    assert outing.wish == "明日方舟 新干员" and outing.found == 2 and outing.shown == 3
    assert outing.sources == ["bilibili_search", "web_search", "ithome"]
    system, menu_text = model.calls[0]
    assert "你之前想找「明日方舟 新干员」" in system
    first = menu_text.splitlines()[1]
    assert first.startswith("[1] 【搜「明日方舟 新干员」】B站｜")
    # only the topic words went out, and only to the search hosts
    bili = next(r for r in asked if r.url.host == "api.bilibili.com")
    assert bili.url.params["keyword"] == "明日方舟 新干员" and "cookie" not in bili.headers
    assert sink.rows[0][0].meta["wish"] == "明日方舟 新干员"
    ds = next(r for r in asked if r.url.host == "api.deepseek.com")
    assert ds.method == "POST" and json.loads(ds.content)["query"] == "明日方舟 新干员"
    assert ds.headers["authorization"] == "Bearer sk-test"
    assert all("authorization" not in r.headers for r in asked if r.url.host != "api.deepseek.com")


def test_no_wish_no_search():
    asked.clear()
    model = FakeModel({"picks": []})
    outing = asyncio.run(go_out([RssSource("ithome", "IT之家", "https://www.ithome.com/rss/")], model, BufferSink(),
                                persona="p", now=NOW, already_seen=set(), searches=[BilibiliSearch()],
                                transport=httpx.MockTransport(handler)))
    assert outing.found == 0 and outing.sources == ["ithome"]
    assert {r.url.host for r in asked} == {"www.ithome.com"}
    assert "想找" not in model.calls[0][0]


def test_wishes():
    st = State()
    assert st.add_wish("  “明日方舟  新干员” ", "想知道", NOW) == (True, "明日方舟 新干员")
    assert not st.add_wish("明日方舟 新干员", "", NOW)[0]  # already there
    assert not st.add_wish("", "", NOW)[0]
    assert not st.add_wish("x" * 31, "", NOW)[0]
    assert not st.add_wish("怎么翻墙", "", NOW, blocked=("翻墙",))[0]
    assert not st.add_wish("13800138000 是谁", "", NOW)[0]
    assert not st.add_wish("a@b.cn", "", NOW)[0]
    assert st.take_wish() == "明日方舟 新干员" and st.take_wish() == ""
    for i in range(10):
        st.add_wish(f"话题{i}", "", NOW)
    assert st.add_wish("再来一个", "", NOW) == (False, "想找的已经攒了太多，先等下次出门")


def test_topic_helpers():
    assert normalize_topic("「原神 4.0」") == "原神 4.0"
    assert looks_personal("QQ 1015")is False and looks_personal("www.example.com")


def test_recent_block_tool_hint_only_for_owner():
    wishes = [{"topic": "明日方舟", "reason": "", "at": NOW.isoformat()}]
    owner = recent_block([], NOW, with_thoughts=True, wishes=wishes, can_wish=True)
    assert "surf_wish" in owner and "「明日方舟」" in owner
    other = recent_block([], NOW, wishes=wishes)
    assert "surf_wish" not in other and "明日方舟" not in other
