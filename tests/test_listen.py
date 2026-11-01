"""Hearing a video: what is kept of the recogniser's pieces, and the reader asking for the audio."""

import asyncio
import io

import httpx
import pytest

from surfing_the_web.bili import BiliReader
from surfing_the_web.listen import Listener, describe
from surfing_the_web.sources import Item, SafeFetcher


def test_describe_keeps_speech_and_names_the_sounds():
    pieces = [(0, "<|zh|><|NEUTRAL|><|Speech|>大家好，今天聊聊匹克球", "Speech"),
              (20, "。", "BGM"), (40, "你一，你我。", "BGM"), (60, "哈哈哈哈这个太好笑了吧", "Laughter"),
              (80, "The。", "BGM")]
    out = describe(pieces)
    assert out.startswith("（听到的声音里有：背景音乐、笑声）")
    assert "[0:00] 大家好，今天聊聊匹克球" in out and "[1:00] 哈哈哈哈这个太好笑了吧" in out
    assert "你一" not in out and "The" not in out and "[0:20]" not in out
    assert describe([(0, "。", "BGM"), (20, "啊", "BGM")]) == "（听到的声音里有：背景音乐；没听到有人说话）"
    assert describe([]) == ""


def test_not_ready_without_the_model(tmp_path):
    ok, why = Listener(tmp_path).ready()
    assert not ok and ("model.int8.onnx" in why or "缺少" in why)


class FakeBili:
    def __init__(self):
        self.asked = []

    def __call__(self, req):
        self.asked.append(req)
        path = req.url.path
        if path == "/x/frontend/finger/spi":
            return httpx.Response(200, json={"code": 0, "data": {"b_3": "d3"}})
        if path == "/x/web-interface/view":
            return httpx.Response(200, json={"code": 0, "data": {"aid": 7, "cid": 8, "desc": "", "pages": []}})
        if path == "/x/player/playurl":
            return httpx.Response(200, json={"code": 0, "data": {"dash": {"audio": [
                {"bandwidth": 190000, "baseUrl": "https://upos-sz-mirrorcos.bilivideo.com/big.m4s"},
                {"bandwidth": 66000, "baseUrl": "https://elsewhere.example.com/small.m4s",
                 "backupUrl": ["https://upos-sz-mirrorcos.bilivideo.com/small.m4s"]}]}}})
        if req.url.host == "upos-sz-mirrorcos.bilivideo.com":
            return httpx.Response(206, content=b"AUDIO")
        return httpx.Response(404)


def test_without_subtitles_she_listens_to_the_smallest_audio_from_an_allowed_host():
    bili, got = FakeBili(), []

    async def hear(audio):
        got.append(audio)
        return "[0:00] 大家好"

    async def go():
        reader = BiliReader({}, hear=hear, listen_seconds=60)
        async with SafeFetcher(set(reader.hosts), transport=httpx.MockTransport(bili)) as f:
            return await reader.read(f, Item(source="bilibili_search", site="B站", title="t", url="u",
                                             meta={"bvid": "BV1xx"}))

    it = asyncio.run(go())
    assert got == [b"AUDIO"] and it.meta["read"] == "heard" and "（这个视频你是怎么看的：没登B站，读不了字幕；听了声音）" in it.body and "大家好" in it.body
    audio = next(r for r in bili.asked if r.url.host == "upos-sz-mirrorcos.bilivideo.com")
    assert audio.url.path == "/small.m4s"  # the smallest stream, from the allowed backup host
    assert audio.headers["range"].startswith("bytes=0-") and "SESSDATA" not in audio.headers.get("cookie", "")
    assert not any(r.url.host == "elsewhere.example.com" for r in bili.asked)


def test_decode_a_real_audio_file():
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    from surfing_the_web.listen import decode

    buf = io.BytesIO()
    with av.open(buf, "w", format="wav") as out:
        stream = out.add_stream("pcm_s16le", rate=44100, layout="stereo")
        frame = av.AudioFrame.from_ndarray(np.zeros((1, 44100 * 3 * 2), dtype=np.int16), format="s16", layout="stereo")
        frame.rate = 44100
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    samples = decode(buf.getvalue(), max_seconds=2)
    assert samples.dtype == np.float32 and abs(samples.size - 32000) < 400  # 2 s at 16 kHz, mono


def test_frames_down_but_heard_she_knows_she_only_listened():
    """No subtitles, the vision model is off, hearing works: she reads that she only heard it, and why."""
    pytest.importorskip("PIL")

    async def hear(audio):
        return "[0:00] 大家好"

    async def see(jpeg, prompt):
        raise ConnectionError("ollama is not running")

    class Shots(FakeBili):
        def __call__(self, req):
            if req.url.path == "/x/player/videoshot":
                return httpx.Response(200, json={"code": 0, "data": {"img_x_len": 1, "img_y_len": 1,
                                                                      "image": ["//bimp.hdslb.com/a.jpg"], "index": [0]}})
            if req.url.host == "bimp.hdslb.com":
                from PIL import Image
                buf = io.BytesIO()
                Image.new("RGB", (16, 9), "red").save(buf, format="PNG")
                return httpx.Response(200, content=buf.getvalue())
            return super().__call__(req)

    async def go():
        reader = BiliReader({}, see, hear=hear, listen_seconds=60)
        async with SafeFetcher(set(reader.hosts), transport=httpx.MockTransport(Shots())) as f:
            it = await reader.read(f, Item(source="bilibili_search", site="B站", title="t", url="u",
                                           meta={"bvid": "BV1xx"}))
            return reader, it

    reader, it = asyncio.run(go())
    assert it.meta["read"] == "heard" and reader.vision_failures == 1
    assert it.meta["how"] == "没登B站，读不了字幕；听了声音；画面没看成，看画面的本机模型没反应"
    assert "画面没看成，看画面的本机模型没反应" in it.body and "大家好" in it.body
