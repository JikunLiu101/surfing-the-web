import asyncio
import json
from datetime import datetime

import httpx

from surfing_the_web.reading import Reader
from surfing_the_web.sources import Item, PageReader, SafeFetcher
from surfing_the_web.wander import BufferSink, go_out, reading

NOW = datetime(2026, 10, 31, 15, 0).astimezone()


def fetch_handler(asked):
    def handler(req):
        asked.append(req)
        if req.url.host == "api.deepseek.com" and req.url.path == "/v1/tools/fetch":
            url = json.loads(req.content)["url"]
            return httpx.Response(200, json={"title": "t", "content": f"# 正文\n\n\n来自 {url} 的[全文](https://x/y)"})
        if req.url.host == "hacker-news.firebaseio.com":
            return httpx.Response(404)
        return httpx.Response(404)
    return handler


def test_short_items_and_search_results_are_read_through_fetch():
    asked = []

    async def go():
        reader = Reader(pages=PageReader("sk-test"))
        async with SafeFetcher(reader.hosts, transport=httpx.MockTransport(fetch_handler(asked))) as f:
            short = await reader(f, Item("hackernews", "Hacker News", "A tool", "https://example.com/a"))
            long = await reader(f, Item("ithome", "IT之家", "长文", "https://www.ithome.com/1", body="字" * 300))
            hit = await reader(f, Item("web_search", "网页", "搜到的", "https://example.com/b", body="摘要" * 200,
                                       meta={"read_page": True}))
            return short, long, hit
    short, long, hit = asyncio.run(go())
    assert short.body == "# 正文\n来自 https://example.com/a 的全文" and short.meta["read"] == "page"
    assert long.body == "字" * 300 and "read" not in long.meta  # the list already had enough
    assert hit.meta["read"] == "page"
    assert {r.url.host for r in asked} == {"api.deepseek.com"}  # pages are downloaded by DeepSeek, not here
    assert all(r.headers["authorization"] == "Bearer sk-test" for r in asked)


def test_secrets_never_follow_a_redirect_to_another_host():
    asked = []

    def handler(req):
        asked.append(req)
        if req.url.host == "api.bilibili.com":
            return httpx.Response(302, headers={"location": "https://i0.hdslb.com/x"})
        return httpx.Response(200, text="ok")

    async def go():
        async with SafeFetcher({"api.bilibili.com", "hdslb.com"}, transport=httpx.MockTransport(handler)) as f:
            return await f.get_text("https://api.bilibili.com/a", headers={"Cookie": "SESSDATA=s", "Referer": "r"})
    assert asyncio.run(go()) == "ok"
    assert asked[0].headers["cookie"] == "SESSDATA=s"
    assert "cookie" not in asked[1].headers and asked[1].headers["referer"] == "r"


def test_reading_explains_what_could_not_be_read():
    it = Item("bilibili_popular", "B站", "冻河", "https://www.bilibili.com/video/BV1", meta={"note": "没读到字幕"})
    assert "（没读到字幕）" in reading([it])
    assert "（只有标题）" in reading([Item("x", "X", "t", "https://x/")])


def test_go_out_reads_what_she_picked_and_blocks_what_turns_out_bad():
    class Src:
        key, site, hosts = "ithome", "IT之家", ("www.ithome.com",)

        async def fetch(self, f, limit):
            return [Item("ithome", "IT之家", "新闻一", "https://www.ithome.com/1"),
                    Item("ithome", "IT之家", "新闻二", "https://www.ithome.com/2")]

        async def open(self, f, item):
            return item

    async def reader(f, item):
        item.body = "里面讲了赌博" if item.url.endswith("2") else "里面讲了显卡"
        return item
    reader.hosts = set()

    answers = [json.dumps({"picks": [1, 2]}), json.dumps({"thoughts": []})]
    calls = []

    async def model(system, user):
        calls.append(user)
        return answers.pop(0)

    outing = asyncio.run(go_out([Src()], model, BufferSink(), persona="小星", now=NOW, already_seen=set(),
                                blocked=("赌博",), reader=reader, rng=__import__("random").Random(1)))
    assert len(outing.seen) == 2
    text = calls[1]
    assert "里面讲了显卡" in text and "赌博" not in text and "（点开一看内容不合适，就关掉了）" in text
