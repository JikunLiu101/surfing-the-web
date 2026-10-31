"""She knows what opening something gets her, notices when it changes, and knows whom to ask when it breaks."""

import asyncio
import json
from datetime import datetime

import httpx

from surfing_the_web.bili import BiliReader, load_login, login_state, mark_expired, save_login
from surfing_the_web.sources import Item, SafeFetcher
from surfing_the_web.wander import recent_block

NOW = datetime(2026, 10, 31, 15, 0).astimezone()


def test_login_states(tmp_path):
    path = tmp_path / "login.json"
    assert login_state(path) == "none"
    save_login(path, {"SESSDATA": "s", "bili_jct": "j", "DedeUserID": "1"})
    assert login_state(path) == "ok" and load_login(path)
    mark_expired(path)
    assert login_state(path) == "expired" and load_login(path) == {}  # not tried again
    save_login(path, {"SESSDATA": "new"})  # scanning again replaces it
    assert login_state(path) == "ok"
    assert "expired" not in json.loads(path.read_text(encoding="utf-8"))


def test_troubles_only_in_the_owners_private_chat():
    trouble = ["B站账号登录过期了：请爸爸重新扫码"]
    owner = recent_block([], NOW, with_thoughts=True, troubles=trouble)
    assert "上网有点不顺" in owner and "请爸爸重新扫码" in owner
    other = recent_block([], NOW, troubles=trouble)
    assert "不顺" not in other and "扫码" not in other


def test_a_vision_model_that_does_not_answer_is_counted():
    def handler(req):
        p = req.url.path
        if p == "/x/frontend/finger/spi":
            return httpx.Response(200, json={"code": 0, "data": {"b_3": "d"}})
        if p == "/x/web-interface/view":
            return httpx.Response(200, json={"code": 0, "data": {"aid": 1, "cid": 2}})
        if p == "/x/player/videoshot":
            return httpx.Response(200, json={"code": 0, "data": {"img_x_len": 1, "img_y_len": 1,
                                                                  "image": ["//bimp.hdslb.com/a.jpg"], "index": [0]}})
        if req.url.host == "bimp.hdslb.com":
            import io

            from PIL import Image
            buf = io.BytesIO()
            Image.new("RGB", (16, 9), "red").save(buf, format="PNG")
            return httpx.Response(200, content=buf.getvalue())
        return httpx.Response(404)

    async def see(jpeg, prompt):
        raise ConnectionError("ollama is not running")

    async def go():
        reader = BiliReader({}, see)
        async with SafeFetcher(set(reader.hosts), transport=httpx.MockTransport(handler)) as f:
            it = await reader.read(f, Item("b", "B站", "t", "https://www.bilibili.com/video/BV1", meta={"bvid": "BV1"}))
            return reader, it
    reader, it = asyncio.run(go())
    assert reader.looks == 1 and reader.vision_failures == 1 and it.meta["read"] == "title"
