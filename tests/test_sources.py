import asyncio
import json

import httpx
import pytest

from surfing_the_web.sources import (
    BlockedURL,
    SafeFetcher,
    build_sources,
    host_allowed,
    html_to_text,
    parse_bilibili,
    parse_dailyhot,
    parse_feed,
    parse_github_trending,
    parse_hn,
    parse_v2ex,
)


def run(coro):
    return asyncio.run(coro)


def test_host_allowed():
    hosts = {"api.bilibili.com", "v2ex.com"}
    assert host_allowed("https://api.bilibili.com/x", hosts)
    assert host_allowed("https://www.v2ex.com/api", hosts)
    assert not host_allowed("https://api.bilibili.com.evil.cn/x", hosts)
    assert not host_allowed("https://evil.cn/?u=api.bilibili.com", hosts)
    assert not host_allowed("file:///etc/passwd", hosts)
    assert not host_allowed("ftp://v2ex.com/", hosts)


def _fetcher(handler, hosts=("ok.test",), **kw):
    return SafeFetcher(set(hosts), transport=httpx.MockTransport(handler), **kw)


def test_fetcher_blocks_other_hosts_and_off_list_redirects():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.path == "/jump":
            return httpx.Response(302, headers={"location": "https://evil.test/steal"})
        if req.url.path == "/inside":
            return httpx.Response(301, headers={"location": "/final"})
        return httpx.Response(200, text="hello")

    async def go():
        async with _fetcher(handler) as f:
            with pytest.raises(BlockedURL):
                await f.get_text("https://evil.test/")
            with pytest.raises(BlockedURL):
                await f.get_text("https://ok.test/jump")
            assert await f.get_text("https://ok.test/inside") == "hello"

    run(go())
    assert not any("evil.test" in u for u in seen)


def test_fetcher_sends_no_cookies_and_caps_size():
    cookies_sent = []

    def handler(req):
        cookies_sent.append(req.headers.get("cookie"))
        if req.url.path == "/big":
            return httpx.Response(200, content=b"x" * 5000)
        return httpx.Response(200, text="ok", headers={"set-cookie": "track=1; Path=/"})

    async def go():
        async with _fetcher(handler, max_bytes=1000) as f:
            await f.get_text("https://ok.test/a")
            await f.get_text("https://ok.test/b")
            with pytest.raises(ValueError):
                await f.get_text("https://ok.test/big")

    run(go())
    assert cookies_sent[:2] == [None, None]


def test_bilibili():
    data = {"code": 0, "data": {"list": [
        {"title": "星露谷 1.7 实机", "bvid": "BV1xx", "desc": "新版本来了", "tname": "单机游戏",
         "owner": {"name": "某UP"}, "stat": {"view": 123456}},
        {"title": "", "bvid": "BV2"},
        {"title": "无简介", "bvid": "BV3", "desc": "-"},
    ]}}
    a, b = parse_bilibili(data, "bilibili_popular", "热门")
    assert a.url == "https://www.bilibili.com/video/BV1xx" and a.body == "新版本来了"
    assert "UP主 某UP" in a.summary and "12.3万" in a.summary and "热门" in a.summary
    assert b.body == ""


RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
 xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>t</title>
<item><title>显卡降价了</title><link>https://www.ithome.com/0/1.htm</link><dc:creator>小编</dc:creator>
<description><![CDATA[<p>RTX <b>5060</b> 降价</p><script>alert(1)</script>]]></description></item>
<item><title></title><link>https://x</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Hello</title>
<link href="https://sspai.com/post/1"/><summary>摘要</summary><author><name>作者甲</name></author></entry></feed>"""


def test_feeds():
    (it,) = parse_feed(RSS, "ithome", "IT之家")
    assert it.title == "显卡降价了" and it.url.endswith("1.htm")
    assert it.body == "RTX 5060 降价" and "alert" not in it.body and it.summary == "作者 小编"
    (at,) = parse_feed(ATOM, "sspai", "少数派")
    assert at.url == "https://sspai.com/post/1" and at.body == "摘要" and "作者甲" in at.summary


def test_v2ex_hn_dailyhot():
    (v,) = parse_v2ex([{"title": "大家用什么键盘", "url": "https://www.v2ex.com/t/1", "content": "求推荐",
                        "replies": 42, "node": {"title": "硬件"}}])
    assert v.body == "求推荐" and "42 条回复" in v.summary
    h = parse_hn([{"type": "story", "id": 7, "title": "Show HN: x", "score": 100, "descendants": 3},
                  {"type": "job", "title": "hiring"}])
    assert len(h) == 1 and h[0].url == "https://news.ycombinator.com/item?id=7"
    (d,) = parse_dailyhot({"title": "知乎", "data": [{"title": "问题", "url": "https://zhihu.com/q/1", "hot": 20000},
                                                    {"title": "no url"}]}, "dailyhot:zhihu")
    assert d.site == "知乎" and "2.0万" in d.summary


def test_github_trending():
    html = """<article class="Box-row"><h2><a href="/foo/bar"> foo /
      bar </a></h2><p> A <b>fast</b> thing </p><span itemprop="programmingLanguage">Rust</span>
      <span class="d-inline-block float-sm-right">1,234 stars today</span></article>"""
    (g,) = parse_github_trending(html)
    assert g.title == "foo/bar" and g.url == "https://github.com/foo/bar"
    assert g.body == "A fast thing" and "Rust" in g.summary and "1,234 stars today" in g.summary


def test_html_to_text_and_build_sources():
    assert html_to_text("<p>a&amp;b</p><style>x</style>") == "a&b"
    srcs = build_sources(["ithome", "nope"], "http://127.0.0.1:6688", ["zhihu"])
    assert [s.key for s in srcs] == ["ithome", "dailyhot:zhihu"]
    assert "127.0.0.1" in srcs[1].hosts
    assert json.dumps([s.key for s in build_sources(["v2ex"])]) == '["v2ex"]'
