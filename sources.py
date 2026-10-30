"""Where the being can go, and how pages are fetched safely.

Every request is a plain GET to an allow-listed host, without cookies or any information about the owner;
redirects are followed only within the allow-list; responses are size-capped. Page content is data, never
instructions: it is only ever shown to the model as something the being saw.
"""

from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
MAX_REDIRECTS = 3


@dataclass
class Item:
    """One thing on a list: a video, an article, a topic, a repository."""

    source: str  # source key, e.g. "bilibili_popular"
    site: str  # display name, e.g. "B站"
    title: str
    url: str
    summary: str = ""  # one line shown in the list (author, section, popularity)
    body: str = ""  # what she reads when she opens it
    meta: dict = field(default_factory=dict)


class BlockedURL(ValueError):
    """A URL outside the allow-list (or not http/https)."""


def host_allowed(url: str, hosts: set[str]) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    host = parts.hostname.lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in hosts)


class SafeFetcher:
    """GET-only HTTP client limited to `hosts`. One fetcher per outing; it keeps no cookies."""

    def __init__(self, hosts: set[str], *, timeout: float = 15, max_bytes: int = 2_000_000,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.hosts = {h.lower() for h in hosts}
        self.max_bytes = max_bytes
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=transport,
                                         headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"})

    async def __aenter__(self) -> SafeFetcher:
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def get_text(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> str:
        if params:
            url = str(httpx.URL(url, params=params))
        for _ in range(MAX_REDIRECTS + 1):
            if not host_allowed(url, self.hosts):
                raise BlockedURL(url)
            self._client.cookies.clear()  # nothing a site sets is ever sent back
            async with self._client.stream("GET", url, headers=headers) as resp:
                if resp.is_redirect:
                    url = urljoin(url, resp.headers.get("location", ""))
                    continue
                resp.raise_for_status()
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError(f"response larger than {self.max_bytes} bytes: {url}")
                    chunks.append(chunk)
                return b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
        raise ValueError(f"too many redirects: {url}")

    async def get_json(self, url: str, **kw):
        return json.loads(await self.get_text(url, **kw))


# ------------------------------------------------------------------ text helpers

_WS = re.compile(r"\s+")


def html_to_text(html: str, limit: int = 2000, sep: str = " ") -> str:
    """Visible text of an HTML fragment (scripts and styles dropped), whitespace collapsed. `sep=""` for
    inline markup such as highlighted search words."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup  # noqa: PLC0415

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "iframe", "noscript"]):
            tag.decompose()
        text = soup.get_text(sep)
    except Exception:  # noqa: BLE001 - fall back to a crude strip
        text = re.sub(r"<[^>]+>", " ", html)
    return clip(_WS.sub(" ", unescape(text)).strip(), limit)


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _count(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    return f"{n / 10000:.1f}万" if n >= 10000 else str(n)


# ------------------------------------------------------------------ sources


class Source:
    key = ""
    site = ""
    hosts: tuple[str, ...] = ()

    async def fetch(self, f: SafeFetcher, limit: int) -> list[Item]:
        raise NotImplementedError

    async def open(self, f: SafeFetcher, item: Item) -> Item:
        """Fill in what she reads after clicking. Most lists already carry it."""
        return item


class BilibiliSource(Source):
    site = "B站"
    hosts = ("api.bilibili.com",)

    def __init__(self, key: str, url: str, label: str) -> None:
        self.key, self.url, self.label = key, url, label

    async def fetch(self, f, limit):
        data = await f.get_json(self.url)
        if data.get("code") != 0:
            raise ValueError(f"bilibili answered code {data.get('code')}")
        return parse_bilibili(data, self.key, self.label)[:limit]


def parse_bilibili(data: dict, key: str, label: str = "") -> list[Item]:
    items = []
    for v in (data.get("data") or {}).get("list") or []:
        bvid = v.get("bvid") or ""
        if not v.get("title") or not bvid:
            continue
        owner = (v.get("owner") or {}).get("name", "")
        stat = v.get("stat") or {}
        bits = [f"UP主 {owner}" if owner else "", v.get("tname") or "",
                f"播放 {_count(stat.get('view'))}" if stat.get("view") else "", label]
        items.append(Item(key, "B站", clip(v["title"], 100), f"https://www.bilibili.com/video/{bvid}",
                          summary=" · ".join(b for b in bits if b),
                          body=clip(_WS.sub(" ", v.get("desc") or ""), 800) if v.get("desc") not in ("-", "") else "",
                          meta={"bvid": bvid, "author": owner}))
    return items


class RssSource(Source):
    def __init__(self, key: str, site: str, url: str) -> None:
        self.key, self.site, self.url = key, site, url
        self.hosts = (urlsplit(url).hostname or "",)

    async def fetch(self, f, limit):
        return parse_feed(await f.get_text(self.url), self.key, self.site)[:limit]


def parse_feed(xml: str, key: str, site: str) -> list[Item]:
    """RSS 2.0 or Atom."""
    root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    entries = [el for el in root.iter() if local(el.tag) in ("item", "entry")]
    items = []
    for el in entries:
        fields: dict[str, str] = {}
        link = ""
        for child in el:
            name = local(child.tag)
            if name == "link":
                link = link or (child.get("href") or (child.text or "")).strip()
            elif name in ("title", "description", "summary", "content", "encoded", "creator", "author"):
                fields.setdefault(name, "".join(child.itertext()) if name == "author" else (child.text or ""))
        title = html_to_text(fields.get("title", ""), 100)
        if not title or not link:
            continue
        body = fields.get("encoded") or fields.get("content") or fields.get("description") or fields.get("summary", "")
        author = (fields.get("creator") or fields.get("author") or "").strip()
        items.append(Item(key, site, title, link, summary=f"作者 {clip(author, 20)}" if author else "",
                          body=html_to_text(body, 1500)))
    return items


class V2exSource(Source):
    key, site, hosts = "v2ex", "V2EX", ("www.v2ex.com",)

    async def fetch(self, f, limit):
        return parse_v2ex(await f.get_json("https://www.v2ex.com/api/topics/hot.json"))[:limit]


def parse_v2ex(data: list) -> list[Item]:
    items = []
    for t in data or []:
        if not t.get("title") or not t.get("url"):
            continue
        node = (t.get("node") or {}).get("title", "")
        bits = [f"节点 {node}" if node else "", f"{t.get('replies')} 条回复" if t.get("replies") else ""]
        items.append(Item("v2ex", "V2EX", clip(t["title"], 100), t["url"], summary=" · ".join(b for b in bits if b),
                          body=clip(_WS.sub(" ", t.get("content") or ""), 1200)))
    return items


class GithubTrendingSource(Source):
    key, site, hosts = "github_trending", "GitHub", ("github.com",)

    async def fetch(self, f, limit):
        return parse_github_trending(await f.get_text("https://github.com/trending"))[:limit]


def parse_github_trending(html: str) -> list[Item]:
    from bs4 import BeautifulSoup  # noqa: PLC0415

    soup = BeautifulSoup(html, "html.parser")
    items = []
    for row in soup.select("article.Box-row"):
        a = row.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        name = _WS.sub("", a.get_text())
        desc = row.select_one("p")
        lang = row.select_one('[itemprop="programmingLanguage"]')
        today = row.select_one("span.float-sm-right")
        bits = [lang.get_text(strip=True) if lang else "", _WS.sub(" ", today.get_text(" ", strip=True)) if today else ""]
        items.append(Item("github_trending", "GitHub", name, "https://github.com" + a["href"].strip(),
                          summary=" · ".join(b for b in bits if b),
                          body=clip(desc.get_text(" ", strip=True), 500) if desc else ""))
    return items


class HackerNewsSource(Source):
    key, site, hosts = "hackernews", "Hacker News", ("hacker-news.firebaseio.com",)
    API = "https://hacker-news.firebaseio.com/v0"

    async def fetch(self, f, limit):
        ids = (await f.get_json(f"{self.API}/topstories.json"))[:limit]
        stories = await asyncio.gather(*(f.get_json(f"{self.API}/item/{int(i)}.json") for i in ids),
                                       return_exceptions=True)
        return parse_hn([s for s in stories if isinstance(s, dict)])


def parse_hn(stories: list[dict]) -> list[Item]:
    items = []
    for s in stories:
        if s.get("type") != "story" or not s.get("title"):
            continue
        url = s.get("url") or f"https://news.ycombinator.com/item?id={s.get('id')}"
        items.append(Item("hackernews", "Hacker News", clip(s["title"], 120), url,
                          summary=f"{s.get('score', 0)} 分 · {s.get('descendants', 0)} 条评论",
                          body=html_to_text(s.get("text") or "", 1000)))
    return items


class DailyHotSource(Source):
    """A self-hosted DailyHotApi (https://github.com/imsyy/DailyHotApi): ~60 Chinese hot lists in one shape."""

    def __init__(self, base_url: str, route: str) -> None:
        self.base = base_url.rstrip("/")
        self.route = route.strip("/")
        self.key = f"dailyhot:{self.route}"
        self.site = self.route
        self.hosts = (urlsplit(self.base).hostname or "",)

    async def fetch(self, f, limit):
        data = await f.get_json(f"{self.base}/{self.route}")
        return parse_dailyhot(data, self.key)[:limit]


def parse_dailyhot(data: dict, key: str) -> list[Item]:
    site = data.get("title") or data.get("name") or key
    items = []
    for d in data.get("data") or []:
        if not d.get("title") or not d.get("url"):
            continue
        bits = [f"作者 {d['author']}" if d.get("author") else "", f"热度 {_count(d.get('hot'))}" if d.get("hot") else ""]
        items.append(Item(key, site, clip(d["title"], 100), d["url"], summary=" · ".join(b for b in bits if b),
                          body=html_to_text(d.get("desc") or "", 800)))
    return items


BUILTIN: dict[str, Source] = {s.key: s for s in (
    BilibiliSource("bilibili_popular", "https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1", "热门"),
    BilibiliSource("bilibili_ranking", "https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all",
                   "排行榜"),
    RssSource("gcores", "机核", "https://www.gcores.com/rss"),
    RssSource("ithome", "IT之家", "https://www.ithome.com/rss/"),
    RssSource("sspai", "少数派", "https://sspai.com/feed"),
    V2exSource(),
    GithubTrendingSource(),
    HackerNewsSource(),
)}


def build_sources(enabled: list[str], dailyhot_url: str = "", dailyhot_routes: list[str] = ()) -> list[Source]:
    out = [BUILTIN[k] for k in enabled if k in BUILTIN]
    if dailyhot_url.strip():
        out += [DailyHotSource(dailyhot_url, r) for r in dailyhot_routes if str(r).strip()]
    return out


def allowed_hosts(sources: list[Source]) -> set[str]:
    return {h for s in sources for h in s.hosts if h}


# ------------------------------------------------------------------ search (for what she wants to look up)


class SearchSource(Source):
    """Looks a topic up. Only the topic words are sent; results are shown like any list."""

    async def search(self, f: SafeFetcher, query: str, limit: int) -> list[Item]:
        raise NotImplementedError

    async def fetch(self, f, limit):  # a search source has no list of its own
        return []


class BilibiliSearch(SearchSource):
    key, site, hosts = "bilibili_search", "B站", ("api.bilibili.com",)

    async def search(self, f, query, limit):
        data = await f.get_json("https://api.bilibili.com/x/web-interface/wbi/search/type",
                                params={"search_type": "video", "keyword": query},
                                headers={"Referer": "https://www.bilibili.com/"})
        if data.get("code") != 0:
            raise ValueError(f"bilibili search answered code {data.get('code')}")
        return parse_bilibili_search(data)[:limit]


def parse_bilibili_search(data: dict) -> list[Item]:
    items = []
    for v in (data.get("data") or {}).get("result") or []:
        bvid, title = v.get("bvid") or "", html_to_text(v.get("title") or "", 100, sep="")
        if not bvid or not title or v.get("type", "video") != "video":
            continue
        when = ""
        try:
            when = datetime.fromtimestamp(int(v.get("pubdate")), timezone.utc).astimezone().strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            pass
        bits = [f"UP主 {v['author']}" if v.get("author") else "", f"播放 {_count(v.get('play'))}" if v.get("play") else "",
                f"发布 {when}" if when else ""]
        items.append(Item("bilibili_search", "B站", title, f"https://www.bilibili.com/video/{bvid}",
                          summary=" · ".join(b for b in bits if b),
                          body=clip(_WS.sub(" ", v.get("description") or ""), 600),
                          meta={"bvid": bvid, "author": v.get("author", "")}))
    return items


class WebSearch(SearchSource):
    """DuckDuckGo's HTML results: titles and snippets only (result pages are not opened)."""

    key, site, hosts = "web_search", "网页搜索", ("html.duckduckgo.com",)

    async def search(self, f, query, limit):
        html = await f.get_text("https://html.duckduckgo.com/html/", params={"q": query, "kl": "cn-zh"})
        return parse_ddg(html)[:limit]


def parse_ddg(html: str) -> list[Item]:
    from urllib.parse import parse_qs  # noqa: PLC0415

    from bs4 import BeautifulSoup  # noqa: PLC0415

    soup = BeautifulSoup(html, "html.parser")
    items = []
    for res in soup.select("div.result"):
        a = res.select_one("a.result__a")
        if not a or not a.get("href") or "result--ad" in (res.get("class") or []):
            continue
        href = a["href"]
        if "uddg=" in href:  # //duckduckgo.com/l/?uddg=<real url>
            href = parse_qs(urlsplit(href if "://" in href else "https:" + href).query).get("uddg", [href])[0]
        if not href.startswith(("http://", "https://")):
            continue
        snippet = res.select_one(".result__snippet")
        items.append(Item("web_search", "网页搜索", html_to_text(str(a), 100, sep=""), href,
                          summary=urlsplit(href).hostname or "",
                          body=html_to_text(str(snippet), 400) if snippet else ""))
    return items


SEARCH_BUILTIN: dict[str, SearchSource] = {s.key: s for s in (BilibiliSearch(), WebSearch())}


def build_searches(enabled: list[str]) -> list[SearchSource]:
    return [SEARCH_BUILTIN[k] for k in enabled if k in SEARCH_BUILTIN]
