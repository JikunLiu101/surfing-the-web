import asyncio
import io
import json

import httpx
import pytest

from surfing_the_web.bili import (BiliReader, choose_subtitle, clock_label, subtitle_belongs, load_login, login_from_url, pick_tiles, pvdata_times,
                                  save_login, subtitle_text)
from surfing_the_web.sources import Item, SafeFetcher

LOGIN = {"SESSDATA": "s3cret", "bili_jct": "jct", "DedeUserID": "42"}


def video(bvid="BV1xx"):
    return Item("bilibili_popular", "B站", "冻河 技能演示", f"https://www.bilibili.com/video/{bvid}",
                meta={"bvid": bvid})


def sheet_png() -> bytes:
    """A 2x2 preview sheet: tiles coloured red, green, blue, white."""
    from PIL import Image

    img = Image.new("RGB", (64, 36))
    for i, colour in enumerate(("red", "green", "blue", "white")):
        img.paste(Image.new("RGB", (32, 18), colour), ((i % 2) * 32, (i // 2) * 18))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class Bili:
    """A fake Bilibili: what is asked, and what it answers."""

    def __init__(self, *, subtitles=True, player_bvid="BV1xx", logged_in=True,
                 sub_lines=("大家好", "今天讲一讲冻河这个干员的技能"), wrong_first=0):
        self.asked = []
        self.subtitles, self.player_bvid, self.logged_in, self.sub_lines = subtitles, player_bvid, logged_in, sub_lines
        self.wrong_first = wrong_first  # this many player answers list another video's AI subtitles

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.asked.append(req)
        path = req.url.path
        if path == "/x/frontend/finger/spi":
            return httpx.Response(200, json={"code": 0, "data": {"b_3": "dev3", "b_4": "dev4"}})
        if path == "/x/web-interface/view":
            return httpx.Response(200, json={"code": 0, "data": {"aid": 7, "cid": 8, "duration": 60, "desc": "六星辅助",
                                                                  "pages": [{"cid": 8, "duration": 60}]}})
        if path == "/x/player/v2":
            subs = [{"lan": "en", "subtitle_url": "//aisubtitle.hdslb.com/en.json"},
                    {"lan": "ai-zh", "subtitle_url": "//aisubtitle.hdslb.com/zh.json"}] if self.subtitles else []
            if self.wrong_first > 0:
                self.wrong_first -= 1
                subs = [{"lan": "ai-zh", "subtitle_url": "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/"
                                                          "113382841652540264981724155805fa9?auth_key=x"}]
            return httpx.Response(200, json={"code": 0, "data": {
                "bvid": self.player_bvid, "cid": 8, "login_mid": 42 if self.logged_in else 0,
                "subtitle": {"subtitles": subs}}})
        if req.url.host == "aisubtitle.hdslb.com":
            body = [{"content": "字幕 by 某某"}] + [{"content": x} for x in self.sub_lines]
            return httpx.Response(200, json={"body": body})
        if path == "/x/player/videoshot":
            return httpx.Response(200, json={"code": 0, "data": {
                "img_x_len": 2, "img_y_len": 2, "image": ["//bimp.hdslb.com/sheet-0001.jpg"], "index": [0, 10, 20, 30]}})
        if req.url.host == "bimp.hdslb.com":
            return httpx.Response(200, content=sheet_png())
        return httpx.Response(404)


def read(bili: Bili, login=LOGIN, see=None, item=None, **kw):
    async def go():
        reader = BiliReader(login, see, **kw)
        async with SafeFetcher(set(reader.hosts), transport=httpx.MockTransport(bili)) as f:
            return reader, await reader.read(f, item or video())
    return asyncio.run(go())


def test_reads_the_subtitles_with_the_login():
    bili = Bili()
    reader, it = read(bili)
    assert it.meta["read"] == "subtitles"
    assert it.body == "简介：六星辅助\n视频里说的（自动识别的字幕，可能有错字）：\n大家好\n今天讲一讲冻河这个干员的技能"
    player = next(r for r in bili.asked if r.url.path == "/x/player/v2")
    assert "SESSDATA=s3cret" in player.headers["cookie"] and "buvid3=dev3" in player.headers["cookie"]
    assert player.url.params["aid"] == "7" and player.url.params["cid"] == "8"
    # the login only goes to the player API; the subtitle file is fetched without it
    for r in bili.asked:
        if r.url.path != "/x/player/v2":
            assert "SESSDATA" not in r.headers.get("cookie", "")
    sub = next(r for r in bili.asked if r.url.host == "aisubtitle.hdslb.com")
    assert sub.url.path == "/zh.json"  # AI Chinese first


def test_another_videos_subtitles_are_not_used():
    _, it = read(Bili(player_bvid="BV9zz"))
    assert it.meta["read"] == "title" and "简介" in it.body


def test_expired_login_is_noticed():
    reader, it = read(Bili(logged_in=False))
    assert reader.login_expired and it.meta["read"] == "title"


def test_credit_only_subtitles_mean_no_speech():
    _, it = read(Bili(sub_lines=("♪",)))
    assert it.meta["read"] == "title"
    assert it.meta["note"] == "没读到这个视频的字幕，画面也没看成，只看到了标题和简介"


def test_without_subtitles_she_looks_at_frames():
    pytest.importorskip("PIL")
    seen = []

    async def see(jpeg, prompt):
        from PIL import Image

        img = Image.open(io.BytesIO(jpeg))
        seen.append((img.size, prompt, img.getpixel((200, 150))))  # away from the time label in the corner
        return "一个人  在雪地里\n放技能"

    bili = Bili(subtitles=False)
    _, it = read(bili, see=see)
    assert it.meta["read"] == "frames"
    assert it.body == "简介：六星辅助\n这个视频没有字幕，你按时间看了里面的一些画面：一个人 在雪地里 放技能"
    (size, prompt, first), = seen
    # the fake sheet has 4 tiles: all 4, in rows of 3, each 480 wide (270 high), with their times in the corner
    assert size == (3 * 480 + 2 * 6, 2 * 270 + 6) and "《冻河 技能演示》" in prompt and "4 个画面" in prompt
    assert first[0] > 200 and first[1] < 60  # tile 0 (red) first: frames are in time order
    shot = next(r for r in bili.asked if r.url.path == "/x/player/videoshot")
    assert "SESSDATA" not in shot.headers.get("cookie", "")


def test_without_login_no_subtitles_are_asked_for():
    bili = Bili()
    _, it = read(bili, login={})
    assert not any(r.url.path == "/x/player/v2" for r in bili.asked) and it.meta["read"] == "title"


def test_looks_are_limited_per_outing():
    calls = []

    async def see(jpeg, prompt):
        calls.append(1)
        return "画面"

    async def go():
        reader = BiliReader({}, see, max_looks=1)
        async with SafeFetcher(set(reader.hosts), transport=httpx.MockTransport(Bili(subtitles=False))) as f:
            return [await reader.read(f, video(b)) for b in ("BV1xx", "BV2yy")]
    first, second = asyncio.run(go())
    assert len(calls) == 1 and first.meta["read"] == "frames" and second.meta["read"] == "title"


def test_helpers(tmp_path):
    assert choose_subtitle([{"lan": "en", "subtitle_url": "a"}, {"lan": "zh-CN", "subtitle_url": "b"}])["lan"] == "zh-CN"
    assert choose_subtitle([{"lan": "ai-zh", "subtitle_url": ""}]) is None
    assert subtitle_text({"body": [{"content": "好"}, {"content": "好"}]}) == ""
    assert pick_tiles([0, 10, 20, 30, 40, 50], 6, 3) == [1, 2, 4]
    assert pick_tiles([0, 0, 0], 3, 3) == [0]
    assert pick_tiles([], 9, 3) == [1, 4, 7]
    assert pvdata_times(b"\x00\x00\x00\x05") == [0.0, 5.0] and pvdata_times(b"\x00") == []
    url = "https://www.bilibili.com/?DedeUserID=42&SESSDATA=s3cret&bili_jct=jct&gourl=x"
    assert login_from_url(url) == {"SESSDATA": "s3cret", "bili_jct": "jct", "DedeUserID": "42"}
    assert login_from_url("https://www.bilibili.com/?a=1") == {}
    path = tmp_path / "login.json"
    assert load_login(path) == {}
    save_login(path, {**LOGIN, "other": "x"})
    assert load_login(path) == LOGIN and "other" not in json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the QR login (2026-10-31: "expired" right after
# confirming: Bilibili sent the session in Set-Cookie, not in data.url, and the used-up key then said 86038)


def _poll(answer: httpx.Response):
    from surfing_the_web.bili import LOGIN_HOSTS, qr_poll

    async def go():
        async with SafeFetcher(set(LOGIN_HOSTS), transport=httpx.MockTransport(lambda req: answer)) as f:
            out = await qr_poll(f, "k")
            leftover = dict(f._client.cookies)
        return out, leftover
    return asyncio.run(go())


def test_qr_login_from_set_cookie():
    answer = httpx.Response(200, json={"code": 0, "data": {"code": 0, "url": "", "refresh_token": "r"}}, headers=[
        ("set-cookie", "SESSDATA=abc%2C1790000000%2Cx1%2A51; Path=/; Domain=bilibili.com; HttpOnly"),
        ("set-cookie", "bili_jct=jct; Path=/; Domain=bilibili.com"),
        ("set-cookie", "DedeUserID=42; Path=/; Domain=bilibili.com"),
        ("set-cookie", "sid=other; Path=/; Domain=bilibili.com"),
    ])
    (code, cookies, seen), leftover = _poll(answer)
    assert code == 0
    assert cookies == {"SESSDATA": "abc%2C1790000000%2Cx1%2A51", "bili_jct": "jct", "DedeUserID": "42"}
    assert leftover == {}  # the fetcher keeps nothing
    assert "abc" not in seen and "SESSDATA" in seen  # names for the log, never the values


def test_qr_login_from_url_is_kept_as_a_browser_sends_it():
    url = ("https://passport.biligame.com/x/passport-login/web/crossDomain?DedeUserID=42&DedeUserID__ckMd5=md5"
           "&SESSDATA=abc%2C1790000000%2Cx1*51&bili_jct=jct&gourl=https%3A%2F%2Fwww.bilibili.com")
    (code, cookies, _), _ = _poll(httpx.Response(200, json={"code": 0, "data": {"code": 0, "url": url}}))
    assert code == 0 and cookies["SESSDATA"] == "abc%2C1790000000%2Cx1%2A51" and cookies["DedeUserID__ckMd5"] == "md5"


def test_qr_confirmed_without_a_login_says_so():
    (code, cookies, seen), _ = _poll(httpx.Response(200, json={"code": 0, "data": {"code": 0, "url": "https://x/?a=1"}}))
    assert code == 0 and cookies == {} and "['a']" in seen
    (code, cookies, seen), _ = _poll(httpx.Response(200, json={"code": 0, "data": {"code": 86090}}))
    assert (code, cookies, seen) == (86090, {}, "")


def test_videos_opened_together_share_one_device_id():
    bili = Bili()

    async def go():
        reader = BiliReader({}, None)
        async with SafeFetcher(set(reader.hosts), transport=httpx.MockTransport(bili)) as f:
            return await asyncio.gather(reader.read(f, video("BV1aa")), reader.read(f, video("BV1bb")))
    asyncio.run(go())
    assert sum(1 for r in bili.asked if r.url.path == "/x/frontend/finger/spi") == 1
    views = [r for r in bili.asked if r.url.path == "/x/web-interface/view"]
    assert len(views) == 2 and all("buvid3=dev3" in r.headers.get("cookie", "") for r in views)


def test_subtitle_ownership():
    mine = {"subtitle_url": "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/117517435600920431205934698cc7ec?auth_key=1"}
    other = {"subtitle_url": "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11338284165254026498172415?auth_key=1"}
    translated = {"subtitle_url": "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/0b7ba54feaf8961acf51bd584fedef2e"}
    uploaded = {"subtitle_url": "//i0.hdslb.com/bfs/subtitle/abc123.json"}
    aid, cid = 117517435600920, 43120593469
    assert subtitle_belongs(mine, aid, cid) and not subtitle_belongs(other, aid, cid)
    assert subtitle_belongs(translated, aid, cid) and subtitle_belongs(uploaded, aid, cid)


def test_another_videos_ai_subtitles_are_skipped_and_asked_again():
    bili = Bili(wrong_first=2)
    _, it = read(bili, retry_pause=0)
    assert it.meta["read"] == "subtitles" and "冻河" in it.body
    assert sum(1 for r in bili.asked if r.url.path == "/x/player/v2") == 3
    assert not any("ai_subtitle" in r.url.path for r in bili.asked)  # the wrong file is never read


def test_only_other_videos_subtitles_means_none():
    bili = Bili(wrong_first=9)
    _, it = read(bili, retry_pause=0)
    assert it.meta["read"] == "title"  # no vision model here: only the title and the description
    assert sum(1 for r in bili.asked if r.url.path == "/x/player/v2") == 5  # asked at most five times


def test_clock_label():
    assert clock_label(0) == "0秒" and clock_label(75) == "1分15秒" and clock_label(75000.0) == "1分15秒"
