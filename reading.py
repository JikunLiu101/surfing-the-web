"""What she gets after clicking something: a video's subtitles or frames, a page's text."""

from __future__ import annotations

from .bili import BiliReader
from .sources import Item, PageReader, SafeFetcher


class Reader:
    """Videos go to the Bilibili reader. Other items whose list gave little to read (or search results, which
    only carry a summary) are read through the page reader. Either can be missing; then the item stays as the
    list showed it."""

    def __init__(self, bili: BiliReader | None = None, pages: PageReader | None = None, *, short: int = 200) -> None:
        self.bili, self.pages, self.short = bili, pages, short

    @property
    def hosts(self) -> set[str]:
        return {h for r in (self.bili, self.pages) if r is not None for h in r.hosts}

    async def __call__(self, f: SafeFetcher, item: Item) -> Item:
        if item.meta.get("bvid"):
            return await self.bili.read(f, item) if self.bili else item
        if self.pages and (item.meta.get("read_page") or len(item.body) < self.short):
            text = await self.pages.read(f, item.url)
            if text:
                item.body = text
                item.meta["read"] = "page"
        return item
