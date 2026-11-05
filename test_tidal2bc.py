import json
import os
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import requests

import tidal2bc

SCRIPT = Path(tidal2bc.__file__)


def track(artist, album, album_id):
    return NS(artist=NS(name=artist), album=NS(id=album_id, name=album) if album_id is not None else None)


def test_albums_to_track_threshold_sorting_and_none_album():
    tracks = [
        track("A", "One", 1), track("A", "One", 1),
        track("B", "Two", 2),
        track("C", "Three", 3), track("C", "Three", 3), track("C", "Three", 3),
        track("D", None, None), track("D", None, None),
    ]
    got = tidal2bc.albums_to_track(tracks)
    assert [(a["artist"], a["title"], a["id"], a["count"]) for a in got] == [
        ("C", "Three", "3", 3),
        ("A", "One", "1", 2),
    ]
    assert [a["id"] for a in tidal2bc.albums_to_track(tracks, min_favs=1)] == ["3", "1", "2"]


@pytest.fixture(autouse=True)
def restore_umask():
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_albums_to_track_skips_tracks_without_artist_or_names():
    tracks = [
        track("A", "One", 1),
        NS(artist=None, album=NS(id=1, name="One")),
        track("", "One", 1),
        track("A", "", 1),
    ]
    assert tidal2bc.albums_to_track(tracks) == []
    assert [a["count"] for a in tidal2bc.albums_to_track(tracks, min_favs=1)] == [1]


def test_albums_to_track_skips_albums_without_id():
    no_id = NS(artist=NS(name="A"), album=NS(id=None, name="One"))
    tracks = [no_id, no_id, track("B", "Two", 2), track("B", "Two", 2)]
    assert [a["id"] for a in tidal2bc.albums_to_track(tracks)] == ["2"]


def test_albums_to_track_prefers_album_artist_and_falls_back_to_track_artist():
    def with_album_artist(album_artist):
        return NS(artist=NS(name="Track Artist"), album=NS(id=1, name="X", artist=album_artist))

    for album_artist, expected in [
        (NS(name="Album Artist"), "Album Artist"),
        (None, "Track Artist"),
        (NS(name=""), "Track Artist"),
    ]:
        got = tidal2bc.albums_to_track([with_album_artist(album_artist)], min_favs=1)
        assert got[0]["artist"] == expected
    assert tidal2bc.albums_to_track([track("T", "X", 1)], min_favs=1)[0]["artist"] == "T"


def test_albums_to_track_groups_by_id_not_name():
    tracks = [track("A", "Same", 1), track("A", "Same", 2), track("A", "Same", 2)]
    assert [(a["id"], a["count"]) for a in tidal2bc.albums_to_track(tracks)] == [("2", 2)]


def write_fixture(path, rows):
    path.write_text(json.dumps([{"artist": a, "album": t, "album_id": i} for a, t, i in rows]))


def run_main(tmp_path, *extra):
    return tidal2bc.main([
        "--tracks-json", str(tmp_path / "tracks.json"),
        "--state", str(tmp_path / "seen.json"),
        "--out", str(tmp_path / "out" / "albums.html"),
        *extra,
    ])


def test_second_run_adds_nothing_new_and_new_albums_come_first(tmp_path, capsys):
    rows = [("A", "One", 1), ("A", "One", 1), ("B", "Two", 2), ("B", "Two", 2)]
    write_fixture(tmp_path / "tracks.json", rows)
    assert run_main(tmp_path) == 0
    assert "2 new" in capsys.readouterr().out
    state = (tmp_path / "seen.json").read_text()

    assert run_main(tmp_path) == 0
    assert "0 new" in capsys.readouterr().out
    assert (tmp_path / "seen.json").read_text() == state

    write_fixture(tmp_path / "tracks.json", rows + [("C", "Three", 3), ("C", "Three", 3)])
    assert run_main(tmp_path) == 0
    assert "1 new" in capsys.readouterr().out
    page = (tmp_path / "out" / "albums.html").read_text()
    assert page.index("Three") < page.index("One")
    assert "Two" in page


def test_html_is_escaped_and_links_open_in_new_tab(tmp_path):
    write_fixture(tmp_path / "tracks.json", [("<b>Ar&t</b>", 'Al"<script>x</script>', 9)] * 2)
    assert run_main(tmp_path) == 0
    page = (tmp_path / "out" / "albums.html").read_text()
    assert "<script>" not in page and "<b>" not in page
    assert "&lt;b&gt;Ar&amp;t&lt;/b&gt;" in page
    assert 'target="_blank"' in page
    assert "https://bandcamp.com/search?q=" in page and '&amp;item_type=a#tidal2bc"' in page


def test_no_session_exits_non_zero_without_waiting(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--state", str(tmp_path / "s.json"), "--out", str(tmp_path / "o.html")],
        env={"HOME": str(tmp_path), "PATH": ""},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20,
    )
    assert r.returncode != 0
    assert "--login" in r.stderr
    assert not (tmp_path / "o.html").exists()


ROWS = [("A", "One", 1), ("A", "One", 1), ("B", "Two", 2), ("B", "Two", 2)]


def test_state_maps_album_id_to_first_seen_timestamp(tmp_path):
    write_fixture(tmp_path / "tracks.json", ROWS)
    assert run_main(tmp_path) == 0
    state = json.loads((tmp_path / "seen.json").read_text())
    assert set(state) == {"1", "2"}
    assert all(datetime.fromisoformat(v) for v in state.values())


def test_page_shows_current_counts_and_drops_albums_that_no_longer_qualify(tmp_path):
    write_fixture(tmp_path / "tracks.json", ROWS)
    run_main(tmp_path)
    first_seen = json.loads((tmp_path / "seen.json").read_text())["2"]

    write_fixture(tmp_path / "tracks.json", [("A", "One", 1)] * 3 + [("B", "Two", 2)])
    run_main(tmp_path)
    page = (tmp_path / "out" / "albums.html").read_text()
    assert "(3 favourites)" in page and "Two" not in page
    assert set(json.loads((tmp_path / "seen.json").read_text())) == {"1", "2"}

    write_fixture(tmp_path / "tracks.json", ROWS)
    run_main(tmp_path)
    assert "Two" in (tmp_path / "out" / "albums.html").read_text()
    assert json.loads((tmp_path / "seen.json").read_text())["2"] == first_seen


def test_raising_min_favs_hides_albums_below_it(tmp_path):
    write_fixture(tmp_path / "tracks.json", [("A", "One", 1)] * 3 + [("B", "Two", 2)] * 2)
    run_main(tmp_path)
    run_main(tmp_path, "--min-favs", "3")
    page = (tmp_path / "out" / "albums.html").read_text()
    assert "One" in page and "Two" not in page


@pytest.mark.parametrize("content, message", [
    ("{not json", "is not valid JSON"),
    ("[]", "is not a JSON object"),
])
def test_bad_state_file_exits_cleanly(tmp_path, capsys, content, message):
    write_fixture(tmp_path / "tracks.json", ROWS)
    (tmp_path / "seen.json").write_text(content)
    assert run_main(tmp_path) == 1
    err = capsys.readouterr().err
    assert f"tidal2bc: state file {tmp_path / 'seen.json'} {message}" in err
    assert "Traceback" not in err
    assert not (tmp_path / "out" / "albums.html").exists()


def test_writes_are_atomic(tmp_path, monkeypatch):
    write_fixture(tmp_path / "tracks.json", ROWS)
    out = tmp_path / "out" / "albums.html"
    out.parent.mkdir()
    out.write_text("OLD")

    def boom(*args):
        raise OSError("boom")

    monkeypatch.setattr(os, "replace", boom)
    assert run_main(tmp_path) == 1
    assert out.read_text() == "OLD"
    assert [p.name for p in out.parent.iterdir()] == ["albums.html"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out", "tracks.json"]


def test_favourites_fetch_error_is_reported_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tidal2bc.tidalapi, "Session", fake_session(pages=[RuntimeError("boom")]))
    rc = run_main_no_tracks(tmp_path)
    err = capsys.readouterr().err
    assert rc == 1
    assert err.startswith("tidal2bc:") and "boom" in err and "Traceback" not in err
    assert "--login" not in err


def test_state_dir_and_file_are_private(tmp_path):
    write_fixture(tmp_path / "tracks.json", ROWS)
    cfg = tmp_path / "cfg"
    tidal2bc.main(["--tracks-json", str(tmp_path / "tracks.json"), "--state", str(cfg / "seen.json"),
                   "--out", str(tmp_path / "albums.html")])
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o700
    assert stat.S_IMODE((cfg / "seen.json").stat().st_mode) == 0o600


def test_login_runs_with_private_umask(tmp_path, monkeypatch):
    seen = {}

    class FakeSession:
        def login_session_file(self, path):
            seen["umask"] = os.umask(0)
            os.umask(seen["umask"])
            path.write_text("{}")
            return True

    monkeypatch.setattr(tidal2bc.tidalapi, "Session", FakeSession)
    session = tmp_path / "cfg" / "tidal-session.json"
    assert tidal2bc.main(["--login", "--session", str(session)]) == 0
    assert seen["umask"] == 0o077
    assert stat.S_IMODE(session.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(session.stat().st_mode) == 0o600


def test_null_album_id_rows_in_tracks_json_are_skipped(tmp_path):
    write_fixture(tmp_path / "tracks.json", ROWS + [("N", "Null", None)] * 2)
    assert run_main(tmp_path) == 0
    assert "Null" not in (tmp_path / "out" / "albums.html").read_text()


@pytest.mark.parametrize("content", [
    None, "{bad", '{"a": 1}', '[{"artist": "A", "album": "X"}]', "[1]",
    '{"tracks": "x"}', '{"albums": [{"artist": "A", "album": "X"}]}', '{"albums": [1]}',
])
def test_bad_tracks_json_exits_cleanly(tmp_path, capsys, content):
    if content is not None:
        (tmp_path / "tracks.json").write_text(content)
    assert run_main(tmp_path) == 1
    err = capsys.readouterr().err
    assert err.startswith("tidal2bc:") and "tracks" in err and "Traceback" not in err
    assert not (tmp_path / "out" / "albums.html").exists()


def test_non_string_state_values_do_not_break_sorting(tmp_path):
    write_fixture(tmp_path / "tracks.json", ROWS)
    (tmp_path / "seen.json").write_text(json.dumps({"1": 5, "2": "2020-01-01T00:00:00+00:00"}))
    assert run_main(tmp_path) == 0


def fake_session(logged_in=True, pages=(), total=None, saved=None, calls=None, album_pages=(), album_total=None):
    """pages[i] is what tracks(limit=50, offset=50*i) returns (a list) or raises (an exception).
    album_pages does the same for albums()."""
    calls = [] if calls is None else calls

    def pager(name, pages, total):
        def count():
            calls.append(f"{name}_count")
            return 50 * len(pages) if total is None else total

        def page(limit, offset):
            calls.append((name, limit, offset))
            result = pages[offset // 50]
            if isinstance(result, Exception):
                raise result
            return result

        return count, page

    get_tracks_count, tracks = pager("tracks", pages, total)
    get_albums_count, albums = pager("albums", album_pages, album_total)

    class FakeSession:
        user = NS(favorites=NS(
            get_tracks_count=get_tracks_count, tracks=tracks,
            get_albums_count=get_albums_count, albums=albums,
            tracks_paginated=lambda: calls.append("tracks_paginated") or [],
            albums_paginated=lambda: calls.append("albums_paginated") or [],
        ))

        def load_session_from_file(self, path):
            return True

        def check_login(self):
            return logged_in

        def save_session_to_file(self, path):
            if saved is not None:
                saved.append(path)

    return FakeSession


@pytest.mark.parametrize("session_class", [
    fake_session(pages=[requests.HTTPError("401", response=NS(status_code=401))]),
    fake_session(logged_in=False),
])
def test_authentication_failures_suggest_login(tmp_path, monkeypatch, capsys, session_class):
    monkeypatch.setattr(tidal2bc.tidalapi, "Session", session_class)
    assert run_main_no_tracks(tmp_path) == 1
    assert "--login" in capsys.readouterr().err


def test_other_http_errors_do_not_suggest_login(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tidal2bc.tidalapi, "Session",
                        fake_session(pages=[requests.HTTPError("503 Server Error", response=NS(status_code=503))]))
    assert run_main_no_tracks(tmp_path) == 1
    err = capsys.readouterr().err
    assert "503 Server Error" in err and "--login" not in err


def run_main_no_tracks(tmp_path):
    return tidal2bc.main(["--session", str(tmp_path / "s.json"), "--state", str(tmp_path / "seen.json"),
                          "--out", str(tmp_path / "albums.html")])


def test_short_pages_are_accepted(tmp_path, monkeypatch):
    calls, saved = [], []
    one = track("A", "One", 1)
    monkeypatch.setattr(tidal2bc.tidalapi, "Session",
                        fake_session(pages=[[one] * 50, [one] * 50, [one] * 18], total=120, calls=calls, saved=saved))
    assert run_main_no_tracks(tmp_path) == 0
    assert "(118 favourites)" in (tmp_path / "albums.html").read_text()
    assert calls == ["tracks_count", ("tracks", 50, 0), ("tracks", 50, 50), ("tracks", 50, 100), "albums_count"]
    assert saved == [tmp_path / "s.json"]


def test_failing_page_fails_cleanly_without_writing_or_saving(tmp_path, monkeypatch, capsys):
    calls, saved = [], []
    pages = [[track("A", "One", 1)] * 50, RuntimeError("boom")]
    monkeypatch.setattr(tidal2bc.tidalapi, "Session", fake_session(pages=pages, total=120, calls=calls, saved=saved))
    assert run_main_no_tracks(tmp_path) == 1
    err = capsys.readouterr().err
    assert err.startswith("tidal2bc:") and "boom" in err and "Traceback" not in err
    assert not (tmp_path / "seen.json").exists() and not (tmp_path / "albums.html").exists()
    assert saved == []
    assert "tracks_paginated" not in calls and "albums_paginated" not in calls


def test_successful_fetch_saves_the_session(tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(tidal2bc.tidalapi, "Session",
                        fake_session(pages=[[track("A", "One", 1)] * 2], saved=saved))
    assert run_main_no_tracks(tmp_path) == 0
    assert saved == [tmp_path / "s.json"]
    assert "One" in (tmp_path / "albums.html").read_text()


def test_invalid_session_message_mentions_expiry_or_outage(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tidal2bc.tidalapi, "Session", fake_session(logged_in=False))
    assert run_main_no_tracks(tmp_path) == 1
    assert capsys.readouterr().err == (
        "tidal2bc: Tidal session not valid (expired, or Tidal is unavailable). "
        "If this persists, run: uv run tidal2bc.py --login\n"
    )


def test_state_is_validated_before_the_tidal_fetch(tmp_path, monkeypatch, capsys):
    calls = []
    (tmp_path / "seen.json").write_text("{not json")
    monkeypatch.setattr(tidal2bc.tidalapi, "Session", fake_session(calls=calls))
    assert run_main_no_tracks(tmp_path) == 1
    assert "is not valid JSON" in capsys.readouterr().err
    assert calls == []


def test_unreadable_state_file_exits_cleanly(tmp_path, capsys):
    write_fixture(tmp_path / "tracks.json", ROWS)
    (tmp_path / "seen.json").mkdir()
    assert run_main(tmp_path) == 1
    err = capsys.readouterr().err
    assert err.startswith("tidal2bc:") and str(tmp_path / "seen.json") in err and "Traceback" not in err


def test_write_errors_name_the_path_and_state_is_written_before_the_page(tmp_path, monkeypatch, capsys):
    write_fixture(tmp_path / "tracks.json", ROWS)
    written = []
    real = tidal2bc.write_atomic

    def fail_on_page(path, text):
        written.append(path.name)
        if path.name == "albums.html":
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr(tidal2bc, "write_atomic", fail_on_page)
    assert run_main(tmp_path) == 1
    assert written == ["seen.json", "albums.html"]
    err = capsys.readouterr().err
    assert str(tmp_path / "out" / "albums.html") in err and "disk full" in err and "Traceback" not in err


def test_writes_are_synced_before_replace(tmp_path, monkeypatch):
    write_fixture(tmp_path / "tracks.json", ROWS)
    calls = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b))[1])
    assert run_main(tmp_path) == 0
    assert calls == ["fsync", "replace", "fsync", "replace"]


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_min_favs_must_be_a_positive_integer(tmp_path, value):
    write_fixture(tmp_path / "tracks.json", ROWS)
    with pytest.raises(SystemExit) as e:
        run_main(tmp_path, "--min-favs", value)
    assert e.value.code == 2


def album(artist, title, album_id):
    return NS(id=album_id, name=title, artist=NS(name=artist) if artist is not None else None)


def test_favourite_albums_qualify_without_track_favourites_and_use_the_album_artist():
    got = tidal2bc.albums_to_track(
        [track("Track Artist", "One", 1)],
        fav_albums=[album("Album Artist", "One", 1), album("F", "Fav", 7)],
    )
    assert [(a["artist"], a["title"], a["id"], a["count"], a["favourite"]) for a in got] == [
        ("Album Artist", "One", "1", 1, True),
        ("F", "Fav", "7", 0, True),
    ]


def test_favourite_albums_skip_missing_ids_names_and_artists():
    bad = [album("A", "X", None), album("A", "", 1), album("", "X", 2), album(None, "X", 3)]
    assert tidal2bc.albums_to_track([], fav_albums=bad) == []


def write_object_fixture(path, tracks, albums):
    rows = lambda items: [{"artist": a, "album": t, "album_id": i} for a, t, i in items]
    path.write_text(json.dumps({"tracks": rows(tracks), "albums": rows(albums)}))


def test_object_fixture_lists_favourite_albums_once_with_their_labels(tmp_path, capsys):
    write_object_fixture(
        tmp_path / "tracks.json",
        [("T", "Both", 1), ("T", "Both", 1), ("T", "Tracks", 2), ("T", "Tracks", 2), ("T", "Single", 3),
         ("T", "FavOne", 8)],
        [("Album Artist", "Both", 1), ("F", "FavOnly", 7), ("F", "FavOne", 8)],
    )
    assert run_main(tmp_path) == 0
    assert "4 new" in capsys.readouterr().out
    page = (tmp_path / "out" / "albums.html").read_text()
    assert page.count("<li>") == 4
    assert "Album Artist - Both</a> (favourite album, 2 favourites)" in page
    assert "T - Tracks</a> (2 favourites)" in page
    assert "F - FavOnly</a> (favourite album)" in page
    assert "F - FavOne</a> (favourite album, 1 favourite)" in page
    assert "Single" not in page
    assert set(json.loads((tmp_path / "seen.json").read_text())) == {"1", "2", "7", "8"}


def test_session_fetch_includes_favourite_albums(tmp_path, monkeypatch):
    calls, saved = [], []
    monkeypatch.setattr(tidal2bc.tidalapi, "Session", fake_session(
        pages=[[track("T", "One", 1)] * 2], album_pages=[[album("A", "Fav", 7)] * 50, [album("A", "Fav2", 8)]],
        album_total=60, calls=calls, saved=saved))
    assert run_main_no_tracks(tmp_path) == 0
    page = (tmp_path / "albums.html").read_text()
    assert "(favourite album)" in page and "Fav2" in page and "T - One</a> (2 favourites)" in page
    assert ("albums", 50, 0) in calls and ("albums", 50, 50) in calls and "albums_paginated" not in calls
    assert saved == [tmp_path / "s.json"]


def test_failing_albums_page_fails_cleanly_without_writing_or_saving(tmp_path, monkeypatch, capsys):
    saved = []
    monkeypatch.setattr(tidal2bc.tidalapi, "Session", fake_session(
        pages=[[track("T", "One", 1)] * 2], album_pages=[RuntimeError("albums boom")], saved=saved))
    assert run_main_no_tracks(tmp_path) == 1
    err = capsys.readouterr().err
    assert err.startswith("tidal2bc:") and "albums boom" in err and "Traceback" not in err
    assert not (tmp_path / "seen.json").exists() and not (tmp_path / "albums.html").exists()
    assert saved == []
