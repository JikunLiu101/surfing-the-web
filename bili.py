"""Reading a Bilibili video: what is said in it (its subtitles), or else what it looks like (a few frames from the
preview sheets Bilibili makes for the progress bar, described by a vision model).

Subtitles need a logged-in account (a QR login: `qr_start` / `qr_poll`); frames do not. Nothing is played or
downloaded beyond the subtitle file and at most a few preview sheets. Read-only: the login is only ever used to
read subtitles and to check who is logged in.

The approach follows dsh-bili-scroll (https://github.com/c1overzz/dsh-bili-scroll) and bilibili-mcp
(https://github.com/adoresever/bilibili-mcp), both MIT: AI Chinese subtitles first, a subtitle that only credits
who made it is not speech, frames come from x/player/videoshot.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import struct
import time
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import parse_qs, quote, urlsplit

from .sources import Item, SafeFetcher, clip

log = logging.getLogger("surfing_the_web")

API = "https://api.bilibili.com"
PASSPORT = "https://passport.bilibili.com/x/passport-login/web/qrcode"
HOSTS = ("api.bilibili.com", "hdslb.com")  # hdslb.com: subtitle files and preview sheets
LOGIN_HOSTS = ("passport.bilibili.com", "api.bilibili.com")
LOGIN_KEYS = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")
REFERER = {"Referer": "https://www.bilibili.com/"}
SUBTITLE_LANGS = ("ai-zh", "zh-CN", "zh-Hans", "zh")

QR_DONE, QR_WAITING, QR_SCANNED, QR_EXPIRED = 0, 86101, 86090, 86038

See = Callable[[bytes, str], Awaitable[str]]  # (jpeg, prompt) -> what the vision model saw

LOOK_PROMPT = ("这张图是B站视频《{title}》里按时间顺序截下来的 {n} 个画面，从左到右拼在一起。"
               "请客观描述画面里有什么：场景、人物或东西、在做什么、画面上能看清的字。"
               "只说看得到的，不猜测，不评价。用中文，150 字以内。")


# ------------------------------------------------------------------ login


def _read_login(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("SESSDATA") else {}


def load_login(path: Path) -> dict:
    """The saved login cookies, or {} when there is none or it has expired."""
    data = _read_login(path)
    if not data or data.get("expired"):
        return {}
    return {k: str(data[k]) for k in LOGIN_KEYS if data.get(k)}


def login_state(path: Path) -> str:
    """"ok", "expired" (Bilibili stopped accepting it; scan again) or "none"."""
    data = _read_login(path)
    return "none" if not data else ("expired" if data.get("expired") else "ok")


def mark_expired(path: Path) -> None:
    """Remember that the saved login no longer works, so it is not tried again and she can be told."""
    data = _read_login(path)
    if data and not data.get("expired"):
        data["expired"] = int(time.time())
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)


def save_login(path: Path, cookies: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**{k: cookies[k] for k in LOGIN_KEYS if cookies.get(k)}, "saved_at": int(time.time())}),
                   encoding="utf-8")
    tmp.replace(path)


def login_from_url(url: str) -> dict:
    """After a confirmed scan, the cookies may come back as query parameters of a cross-domain URL. They are kept
    the way a browser would send them (SESSDATA's commas and star percent-encoded)."""
    query = parse_qs(urlsplit(url or "").query)
    out = {k: quote(query[k][0], safe="") for k in LOGIN_KEYS if query.get(k)}
    return out if out.get("SESSDATA") else {}


def login_from_cookies(cookies: dict) -> dict:
    """The login cookies Bilibili set on the confirmed poll (Set-Cookie)."""
    out = {k: str(cookies[k]) for k in LOGIN_KEYS if cookies.get(k)}
    return out if out.get("SESSDATA") else {}


async def qr_start(f: SafeFetcher) -> tuple[str, str]:
    """A new login QR code: (the URL to encode, the key to poll with)."""
    data = await f.get_json(f"{PASSPORT}/generate", headers=REFERER)
    if data.get("code") != 0:
        raise ValueError(f"bilibili login answered code {data.get('code')}")
    return data["data"]["url"], data["data"]["qrcode_key"]


async def qr_poll(f: SafeFetcher, key: str) -> tuple[int, dict, str]:
    """(QR_DONE / QR_WAITING / QR_SCANNED / QR_EXPIRED or another code, the cookies once done, and on QR_DONE
    which cookie and URL parameter *names* came back, for the log; never the values).

    A confirmed key is used up: polling it again answers "expired". So on QR_DONE the cookies must be taken from
    this very answer, from its Set-Cookie headers (what Bilibili does now) or else from `data.url`."""
    data, set_cookies = await f.get_json_with_cookies(f"{PASSPORT}/poll", params={"qrcode_key": key},
                                                      headers=REFERER)
    d = data.get("data") or {}
    code = int(d.get("code", -1))
    if code != QR_DONE:
        return code, {}, ""
    url = d.get("url") or ""
    cookies = login_from_cookies(set_cookies) or login_from_url(url)
    seen = (f"set-cookie {sorted(set_cookies)}, url parameters {sorted(parse_qs(urlsplit(url).query))}, "
            f"data keys {sorted(d)}")
    return code, cookies, seen


def cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)


async def whoami(f: SafeFetcher, login: dict) -> str:
    """The name of the logged-in account, or "" when the login does not work (any more)."""
    if not login:
        return ""
    data = await f.get_json(f"{API}/x/web-interface/nav", headers={**REFERER, "Cookie": cookie_header(login)})
    d = data.get("data") or {}
    return str(d.get("uname") or "") if data.get("code") == 0 and d.get("isLogin") else ""


# ------------------------------------------------------------------ subtitles


def choose_subtitle(subtitles: list[dict]) -> dict | None:
    """AI Chinese subtitles first, then any Chinese, then the first one with a file."""
    usable = [s for s in subtitles or [] if s.get("subtitle_url") or s.get("subtitle_url_v2")]
    for lang in SUBTITLE_LANGS:
        for s in usable:
            if s.get("lan") == lang:
                return s
    return usable[0] if usable else None


_CREDIT = re.compile(r"字幕\s*(by|制作|提供|翻译|校对)|^\s*(by|字幕)\s*[:：]", re.I)


def subtitle_text(body: dict, limit: int = 2500, min_chars: int = 15) -> str:
    """The lines of a subtitle file as text. Lines that only credit who made the subtitles are not speech; a
    subtitle with almost nothing else means the video has no speech (music, a silent demo): ""."""
    lines, last = [], None
    for seg in (body or {}).get("body") or []:
        line = re.sub(r"\s+", " ", str(seg.get("content") or "")).strip()
        if not line or line == last or _CREDIT.search(line):
            continue
        lines.append(line)
        last = line
    text = "\n".join(lines)
    return clip(text, limit) if len(text.replace("\n", "")) >= min_chars else ""


# ------------------------------------------------------------------ frames


def pick_tiles(times: list[float], total: int, n: int) -> list[int]:
    """`n` preview tiles spread over the video: the tile closest to 1/6, 3/6, 5/6 of the way (for n = 3).
    `times` are the tiles' times (seconds or milliseconds, only their order matters); without them, tiles are
    spread over `total`."""
    count = min(len(times), total) if times else total
    if count <= 0 or n <= 0:
        return []
    if not times:
        return sorted({min(count - 1, int(count * (2 * k + 1) / (2 * n))) for k in range(n)})
    times = times[:count]
    end = max(times) or 1.0
    picked: list[int] = []
    for k in range(n):
        target = end * (2 * k + 1) / (2 * n)
        best = min(range(count), key=lambda i: (abs(times[i] - target), i))
        if best not in picked:
            picked.append(best)
    return sorted(picked)


def pvdata_times(raw: bytes) -> list[float]:
    """The fallback list of tile times: big-endian 16-bit seconds."""
    if not raw or len(raw) % 2 or len(raw) > 40000:
        return []
    return [float(x) for x in struct.unpack(f">{len(raw) // 2}H", raw)]


def crop_tile(sheet: bytes, index: int, cols: int, rows: int):
    """Tile `index` of a preview sheet (cols x rows tiles), as a Pillow image."""
    from PIL import Image  # noqa: PLC0415 - Pillow comes with AstrBot

    img = Image.open(io.BytesIO(sheet))
    tw, th = img.width // max(1, cols), img.height // max(1, rows)
    col, row = index % cols, index // cols
    if row >= rows:
        raise ValueError(f"tile {index} is outside a {cols}x{rows} sheet")
    return img.crop((col * tw, row * th, (col + 1) * tw, (row + 1) * th)).convert("RGB")


def stitch(frames: list, height: int = 270) -> bytes:
    """Frames side by side, same height, as one JPEG (one picture for the vision model, not several)."""
    from PIL import Image  # noqa: PLC0415

    scaled = [f.resize((max(1, round(f.width * height / f.height)), height)) for f in frames]
    out = Image.new("RGB", (sum(f.width for f in scaled) + 8 * (len(scaled) - 1), height), "white")
    x = 0
    for f in scaled:
        out.paste(f, (x, 0))
        x += f.width + 8
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _https(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


# ------------------------------------------------------------------ reading a video


class BiliReader:
    """Fills in what she gets from opening a Bilibili video. One reader per outing."""

    hosts = HOSTS

    def __init__(self, login: dict | None = None, see: See | None = None, *, frames: int = 3, max_looks: int = 2,
                 subtitle_limit: int = 2500) -> None:
        self.login = dict(login or {})
        self.see = see
        self.frames, self.max_looks, self.subtitle_limit = frames, max_looks, subtitle_limit
        self.looks = 0  # videos looked at with the vision model in this outing (it is slow)
        self.vision_failures = 0  # of those, how many the vision model did not answer
        self.login_expired = False
        self._device: dict | None = None
        self._device_lock: asyncio.Lock | None = None

    async def _device_cookies(self, f: SafeFetcher) -> dict:
        """A device id (buvid3/buvid4), handed out by Bilibili to anyone: without it, video pages answer 412.
        The videos of an outing are opened at the same time; they all wait for the one id."""
        if self._device is None:
            if self._device_lock is None:
                self._device_lock = asyncio.Lock()
            async with self._device_lock:
                if self._device is None:
                    device = {}
                    try:
                        d = (await f.get_json(f"{API}/x/frontend/finger/spi", headers=REFERER)).get("data") or {}
                        device = {k: v for k, v in (("buvid3", d.get("b_3")), ("buvid4", d.get("b_4"))) if v}
                    except Exception as exc:  # noqa: BLE001 - try without it
                        log.warning("no bilibili device id (videos may answer 412): %s", exc)
                    self._device = device
        return self._device

    async def _api(self, f: SafeFetcher, path: str, params: dict, *, logged_in: bool = False) -> dict:
        cookies = {**await self._device_cookies(f), **(self.login if logged_in else {})}
        headers = {**REFERER, **({"Cookie": cookie_header(cookies)} if cookies else {})}
        data = await f.get_json(API + path, params=params, headers=headers)
        if data.get("code") != 0:
            raise ValueError(f"bilibili {path} answered code {data.get('code')}")
        return data.get("data") or {}

    async def read(self, f: SafeFetcher, item: Item) -> Item:
        bvid = item.meta.get("bvid") or ""
        if not bvid:
            return item
        view = await self._api(f, "/x/web-interface/view", {"bvid": bvid})
        pages = view.get("pages") or []
        aid, cid = view.get("aid"), view.get("cid") or (pages[0].get("cid") if pages else None)
        desc = re.sub(r"\s+", " ", str(view.get("desc") or "")).strip()
        desc = "" if desc in ("-", "") else desc
        parts = [f"简介：{clip(desc, 300)}"] if desc else []

        said = ""
        if self.login and aid and cid:
            try:
                said = await self.subtitles(f, bvid, aid, cid)
            except Exception as exc:  # noqa: BLE001 - she can still look
                log.warning("subtitles of %s: %s", bvid, exc)
        looked = ""
        if not said and self.see and cid and self.looks < self.max_looks:
            self.looks += 1
            try:
                looked = await self.look(f, bvid, cid, item.title)
            except Exception as exc:  # noqa: BLE001
                log.warning("frames of %s: %s", bvid, exc)

        if said:
            parts.append("视频里说的（自动识别的字幕，可能有错字）：\n" + said)
            item.meta["read"] = "subtitles"
        elif looked:
            parts.append(f"这个视频没有字幕，你看了里面几个画面：{looked}")
            item.meta["read"] = "frames"
        else:
            item.meta["read"] = "title"
            item.meta["note"] = "没读到这个视频的字幕，画面也没看成，只看到了标题" + ("和简介" if desc else "")
        item.body = "\n".join(parts)
        return item

    async def subtitles(self, f: SafeFetcher, bvid: str, aid, cid) -> str:
        data = await self._api(f, "/x/player/v2", {"aid": aid, "cid": cid}, logged_in=True)
        if "login_mid" in data and not data.get("login_mid"):
            self.login_expired = True  # the saved login no longer works
            return ""
        if str(data.get("bvid") or bvid) != bvid or str(data.get("cid") or cid) != str(cid):
            log.warning("bilibili answered with another video's player info for %s", bvid)
            return ""
        chosen = choose_subtitle((data.get("subtitle") or {}).get("subtitles") or [])
        if not chosen:
            return ""
        url = _https(chosen.get("subtitle_url") or chosen.get("subtitle_url_v2") or "")
        raw, _ = await f.get_bytes(url, headers=REFERER)  # no login here: the file is public
        return subtitle_text(json.loads(raw.decode("utf-8", errors="replace")), self.subtitle_limit)

    async def look(self, f: SafeFetcher, bvid: str, cid, title: str) -> str:
        """A few frames (multi-part videos: the preview covers part 1), described by the vision model."""
        shot = await self._api(f, "/x/player/videoshot", {"bvid": bvid, "cid": cid, "index": 1})
        sheets = [_https(u) for u in shot.get("image") or [] if u]
        cols, rows = int(shot.get("img_x_len") or 10), int(shot.get("img_y_len") or 10)
        times = [float(t) for t in shot.get("index") or [] if isinstance(t, (int, float))]
        if not times and shot.get("pvdata"):
            try:
                times = pvdata_times((await f.get_bytes(_https(shot["pvdata"]), headers=REFERER))[0])
            except Exception as exc:  # noqa: BLE001
                log.debug("pvdata of %s: %s", bvid, exc)
        if not sheets or not times:  # without times the last sheet's empty tiles cannot be told apart
            return ""
        per = cols * rows
        tiles = pick_tiles(times, len(sheets) * per, self.frames)
        got: dict[int, bytes] = {}
        frames = []
        for t in tiles:
            k = t // per
            if k not in got:
                got[k] = (await f.get_bytes(sheets[k], headers=REFERER, max_bytes=8_000_000))[0]
            frames.append(crop_tile(got[k], t % per, cols, rows))
        if not frames:
            return ""
        try:
            seen = await self.see(stitch(frames), LOOK_PROMPT.format(title=title, n=len(frames)))
        except Exception:
            self.vision_failures += 1
            raise
        return clip(re.sub(r"\s+", " ", seen or "").strip(), 400)
