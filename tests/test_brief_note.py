from datetime import datetime, timedelta

from surfing_the_web.wander import NoteMemo, brief_block, recent_block

NOW = datetime(2026, 10, 30, 15, 0).astimezone()
SEEN = [{"at": (NOW - timedelta(hours=2)).isoformat(), "site": "B站", "title": "罗小黑战记", "thought": "好可爱"},
        {"at": (NOW - timedelta(hours=1)).isoformat(), "site": "IT之家", "title": "新显卡", "thought": "好贵"}]


def test_full_note_at_the_start_of_a_talk_and_when_it_changes():
    memo, chat = NoteMemo(), "telegram:private:1"
    full = recent_block(SEEN, NOW, with_thoughts=True, self_left=2)
    assert memo.full_due(chat, full, NOW, 30)  # nothing sent yet
    assert not memo.full_due(chat, full, NOW + timedelta(minutes=2), 30)  # the same talk
    assert not memo.full_due(chat, full, NOW + timedelta(minutes=25), 30)  # each reply keeps the talk going
    assert memo.full_due(chat, full, NOW + timedelta(minutes=60), 30)  # quiet for half an hour: a new talk
    changed = recent_block(SEEN, NOW, with_thoughts=True, self_left=1)  # she went out meanwhile
    assert memo.full_due(chat, changed, NOW + timedelta(minutes=61), 30)
    assert memo.full_due("other", full, NOW, 30)  # each chat on its own
    assert memo.full_due(chat, changed, NOW + timedelta(minutes=62), 0)  # 0: always the full note


def test_brief_note_is_short_and_keeps_titles_and_count():
    full = recent_block(SEEN, NOW, with_thoughts=True, can_wish=True, self_left=2, self_ready="16:00", asked_left=10)
    brief = brief_block(SEEN, NOW, self_left=2, self_ready="16:00", asked_left=10)
    assert brief.startswith("<surfing_the_web>") and brief.endswith("</surfing_the_web>")
    assert "《罗小黑战记》、《新显卡》" in brief and "还能自己上网 2 次（16:00以后）" in brief
    assert "好可爱" not in brief and len(brief) < len(full) / 2
    used_up = brief_block([], NOW, self_left=0, asked_left=10)
    assert "次数用完了" in used_up and "asked=true" in used_up
    assert brief_block([], NOW) == ""  # nothing to say outside the owner's chat with nothing seen
