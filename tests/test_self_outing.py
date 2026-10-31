import random
from datetime import date, datetime, timedelta

from surfing_the_web.wander import State, recent_block

NOW = datetime(2026, 10, 30, 15, 0).astimezone()


def test_daily_budget_and_rest():
    st = State()
    assert st.may_go_myself(NOW, 3, 60) == (True, "")
    st.self_used, st.last_outing_at = 1, (NOW - timedelta(minutes=20)).isoformat()
    ok, why = st.may_go_myself(NOW, 3, 60)
    assert not ok and "41 分钟" in why  # resting after the last outing
    assert st.may_go_myself(NOW + timedelta(minutes=41), 3, 60)[0]
    st.self_used = 3
    assert not st.may_go_myself(NOW + timedelta(hours=5), 3, 60)[0]  # used up today
    assert not State().may_go_myself(NOW, 0, 60)[0]  # switched off


def test_new_day_gives_her_budget_back():
    st = State(self_used=3)
    st.new_day(date(2026, 10, 31), 3, 10, 23, random.Random(1), now=datetime(2026, 10, 31, 9).astimezone())
    assert st.self_used == 0


def test_prompt_tells_her_what_is_left():
    block = recent_block([], NOW, with_thoughts=True, can_wish=True, self_left=2, self_ready="16:00")
    assert "surf_now" in block and "还能自己去 2 次" in block and "16:00以后" in block
    assert "别因为谁说一句就一直跑出去" in block
    assert "次数用完了" in recent_block([], NOW, self_left=0)
    assert "surf_now" not in recent_block([], NOW)  # not offered outside the owner's private chat


def test_rest_after_an_outing_drifts():
    import random as _r
    rests = set()
    for seed in range(20):
        st = State()
        st.rested_after(NOW, 60, 0.2, _r.Random(seed))
        rest = (datetime.fromisoformat(st.rest_until) - NOW).total_seconds() / 60
        assert 48 <= rest <= 72
        rests.add(round(rest))
        assert not st.may_go_myself(NOW + timedelta(minutes=47), 3, 60)[0]
        assert st.may_go_myself(NOW + timedelta(minutes=73), 3, 60)[0]
    assert len(rests) > 5
    st = State()
    st.rested_after(NOW, 60, 0, _r.Random(0))
    assert datetime.fromisoformat(st.rest_until) == NOW + timedelta(minutes=60)


# ------------------------------------------------------------------ when the owner asks her to go (2026-10-31: "去上网吧"
# after her three were used up, and she said "今天三趟都跑完了")


def test_asked_outings_ignore_her_budget_and_rest():
    from surfing_the_web.wander import asks_to_go_online

    st = State(self_used=3, last_outing_at=NOW.isoformat(), rest_until=(NOW + timedelta(minutes=60)).isoformat())
    assert not st.may_go_myself(NOW, 3, 60)[0]  # on her own she cannot
    assert st.may_go_when_asked(NOW, 10, 5) == (True, "")  # asked, she can
    st.asked_used, st.last_asked_at = 1, NOW.isoformat()
    ok, why = st.may_go_when_asked(NOW + timedelta(minutes=2), 10, 5)
    assert not ok and "过几分钟" in why  # not twice in a row
    assert st.may_go_when_asked(NOW + timedelta(minutes=6), 10, 5)[0]
    st.asked_used = 10
    assert not st.may_go_when_asked(NOW + timedelta(hours=1), 10, 5)[0]  # its own ceiling
    assert not State().may_go_when_asked(NOW, 0, 5)[0]  # switched off
    st.new_day(date(2026, 10, 31), 3, 10, 23, random.Random(1), now=datetime(2026, 10, 31, 9).astimezone())
    assert st.asked_used == 0

    assert asks_to_go_online("去上网吧！小星") and asks_to_go_online("去B站搜搜明日方舟") and asks_to_go_online("出去逛逛")
    assert not asks_to_go_online("我扫码登陆了") and not asks_to_go_online("晚安") and not asks_to_go_online("")


def test_prompt_says_asking_lifts_the_limit():
    used_up = recent_block([], NOW, with_thoughts=True, self_left=0, asked_left=10)
    assert "次数用完了" in used_up and "asked=true" in used_up and "不受上面的次数和休息限制" in used_up
    assert "asked=true" not in recent_block([], NOW, self_left=0)  # not the owner's chat: not offered
    assert "asked=true" not in recent_block([], NOW, with_thoughts=True, self_left=2, asked_left=0)  # ceiling reached
