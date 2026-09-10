"""format_artists / format_album 单元测试

样例摘自 logs/rsp_song_detail.json / rsp_song_detail2.json：
- 正常单人 / 合唱
- 云盘曲：ar/al 为空，真实信息在 pc
- 云盘脏数据：name 含「歌手 - 歌名」，ar.name / al.name 为 null
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_api import format_album, format_artists  # noqa: E402

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "song_artists_samples.json"


@pytest.fixture(scope="module")
def samples():
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def test_single_artist_from_song(samples):
    song = samples["single_artist"]
    assert format_artists(song) == "G.E.M.邓紫棋"
    assert format_artists(song["ar"]) == "G.E.M.邓紫棋"
    assert format_album(song) == song["al"]["name"]


def test_two_artists_duet(samples):
    song = samples["two_artists"]
    assert format_artists(song) == "成龙/金喜善"
    assert format_artists(song["ar"]) == "成龙/金喜善"


def test_three_artists_chorus(samples):
    song = samples["three_artists"]
    assert format_artists(song) == "Justin Timberlake/Carey Mulligan/Stark Sands"


def test_two_artists_english(samples):
    song = samples["two_artists_english"]
    assert format_artists(song) == "Jonathan Steingard/Hawk Nelson"


def test_cloud_song_fallback_pc(samples):
    """夜曲：ar/al 为空字符串，应从 pc 回退。"""
    song = samples["cloud_empty_ar_al"]
    assert song["name"] == "夜曲"
    assert song["ar"][0]["name"] == ""
    assert song["al"]["name"] == ""
    assert format_artists(song) == "周杰伦"
    assert format_album(song) == "11月的萧邦（10周年珍藏版）"
    # 只传 ar 时无法读 pc，仍为空
    assert format_artists(song["ar"]) == ""


def test_null_artist_and_album_fallback_title(samples):
    """朴树云盘曲：ar.name/al.name 为 null，pc.ar/alb 也为空，从标题解析歌手。"""
    song = samples["null_artist_name"]
    assert song["ar"][0]["name"] is None
    assert song["al"]["name"] is None
    assert format_artists(song) == "朴树"
    assert format_album(song) == ""  # 无专辑信息时返回空串，不返回 None
    assert format_artists(song["ar"]) == ""


def test_duet_chinese_from_detail2(samples):
    song = samples["duet_chinese"]
    assert format_artists(song) == "杨宗纬/张碧晨"
    assert format_album(song) == song["al"]["name"]


def test_empty_and_none_inputs():
    assert format_artists([]) == ""
    assert format_artists(None) == ""
    assert format_artists({}) == ""
    assert format_artists({"ar": []}) == ""
    assert format_artists({"ar": None}) == ""
    assert format_album(None) == ""
    assert format_album({}) == ""
    assert format_album({"al": {"name": None}}) == ""


def test_skips_empty_string_names():
    ar = [{"name": "A"}, {"name": ""}, {"name": "B"}, {"name": None}]
    assert format_artists(ar) == "A/B"


def test_mixed_null_among_valid_artists():
    ar = [{"name": None}, {"name": "张三"}, {"name": "李四"}]
    assert format_artists(ar) == "张三/李四"


def test_title_artist_dash_without_spaces():
    song = {"name": "古筝-千本樱", "ar": [{"name": None}], "al": {"name": None}, "pc": {}}
    assert format_artists(song) == "古筝"
    assert format_album(song) == ""
