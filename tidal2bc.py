# /// script
# requires-python = ">=3.10"
# dependencies = ["tidalapi==0.8.11"]
# ///
"""List Tidal albums you favourited, or with at least N favourite tracks, as a page of Bandcamp search links."""
import argparse
import html
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

import tidalapi
from tidalapi.exceptions import AuthenticationError

CONFIG_DIR = Path.home() / ".config" / "tidal2bc"


def albums_to_track(tracks, min_favs=2, fav_albums=()):
    """Merge favourite albums and favourite tracks by album id.

    An album qualifies if it is a favourite album or has >= min_favs favourite tracks; most track favourites first.
    """
    found = {}
    for a in fav_albums:
        if a.id is None or not a.name or a.artist is None or not a.artist.name:
            continue
        found[str(a.id)] = {"artist": a.artist.name, "title": a.name, "id": str(a.id), "count": 0, "favourite": True}
    for t in tracks:
        if t.album is None or t.album.id is None or t.artist is None or not t.album.name or not t.artist.name:
            continue
        key = str(t.album.id)
        # shortcut: tidalapi fills album.artist from the track's own artists for favourites (media.py:247),
        # so this is the first favourited track's artist; wrong for compilations
        artist = getattr(getattr(t.album, "artist", None), "name", None) or t.artist.name
        entry = found.setdefault(key, {"artist": artist, "title": t.album.name, "id": key, "count": 0, "favourite": False})
        entry["count"] += 1
    kept = [a for a in found.values() if a["favourite"] or a["count"] >= min_favs]
    return sorted(kept, key=lambda a: -a["count"])  # sorted() is stable


def bandcamp_search_url(album):
    # The hash is never sent to Bandcamp; the userscript reads it to know the page was opened from this list.
    return f"https://bandcamp.com/search?q={quote_plus(album['artist'] + ' ' + album['title'])}&item_type=a#tidal2bc"


def favourites_label(album):
    if not album["favourite"]:
        return str(album["count"])
    return "favourite album" + (f", {album['count']}" if album["count"] else "")


def link(url, text):
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{text}</a>'


def render_page(albums):
    rows = "\n".join(
        f'<tr><td>{html.escape(a["artist"])}</td><td>{html.escape(a["title"])}</td><td>{favourites_label(a)}</td>'
        f'<td>{link("https://tidal.com/browse/album/" + quote(a["id"], safe=""), "Tidal")}</td>'
        f'<td>{link(bandcamp_search_url(a), "Search")}</td></tr>'
        for a in albums
    )
    return (
        '<!doctype html>\n<meta charset="utf-8">\n<title>Tidal albums for Bandcamp</title>\n'
        "<style>table{border-collapse:collapse}th,td{border:1px solid #ccc;padding:.25em .5em}</style>\n"
        f"<h1>Tidal albums for Bandcamp ({len(albums)})</h1>\n<table>\n"
        "<thead><tr><th>Artist</th><th>Album</th><th>Favourites</th><th>Tidal</th><th>Bandcamp</th></tr></thead>\n"
        f"<tbody>\n{rows}\n</tbody>\n</table>\n"
    )


def load_state(path):
    """Return {album id: first-seen ISO timestamp}; raise ValueError if the file is unusable."""
    try:
        state = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        raise ValueError(f"state file {path} is not valid JSON")
    except OSError as e:
        raise ValueError(f"cannot read state file {path}: {e}")
    if not isinstance(state, dict):
        raise ValueError(f"state file {path} is not a JSON object")
    return state


def write_atomic(path, text):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def load_tracks_json(path):
    """Return (fake tracks, fake favourite albums) from a list of tracks or {"tracks": [...], "albums": [...]}.

    Rows are {artist, album, album_id}. Raise ValueError if the file is unusable.
    """
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read tracks file {path}: {e}")
    if isinstance(data, list):
        data = {"tracks": data}
    if not isinstance(data, dict) or not data.keys() & {"tracks", "albums"}:
        raise ValueError(f'tracks file {path} must be a JSON list or an object with "tracks" and/or "albums" lists')
    if not all(isinstance(data.get(k, []), list) for k in ("tracks", "albums")):
        raise ValueError(f'tracks file {path}: "tracks" and "albums" must be JSON lists')

    def build_track(r):
        return SimpleNamespace(artist=SimpleNamespace(name=r["artist"]), album=SimpleNamespace(id=r["album_id"], name=r["album"]))

    def build_album(r):
        return SimpleNamespace(id=r["album_id"], name=r["album"], artist=SimpleNamespace(name=r["artist"]))

    def rows(kind, build):
        built = []
        for i, r in enumerate(data.get(kind, [])):
            try:
                built.append(build(r))
            except (KeyError, TypeError):
                raise ValueError(f"tracks file {path}: {kind} row {i} must be an object with artist, album and album_id")
        return built

    return rows("tracks", build_track), rows("albums", build_album)


class TidalError(Exception):
    """An expected Tidal failure whose message is shown as is."""


def fetch_favourites(session, session_path):
    """Return (favourite tracks, favourite albums)."""
    if not session.load_session_from_file(session_path):
        raise TidalError(f"no Tidal session at {session_path}. Run: uv run tidal2bc.py --login")
    if not session.check_login():
        raise TidalError(f"Tidal session not valid (expired, or Tidal is unavailable). If this persists, run: uv run tidal2bc.py --login")
    favourites = session.user.favorites

    def fetch_all(count, page):
        # Not *_paginated: its worker (tidalapi/workers.py:func_wrapper) swallows page errors and returns fewer items.
        # Pages can be short (unavailable items), so the count only bounds the offsets.
        return [item for offset in range(0, count, 50) for item in page(limit=50, offset=offset)]

    tracks = fetch_all(favourites.get_tracks_count(), favourites.tracks)
    albums = fetch_all(favourites.get_albums_count(), favourites.albums)
    session.save_session_to_file(session_path)  # persists tokens tidalapi refreshed during the run
    return tracks, albums


def is_auth_error(e):
    return isinstance(e, AuthenticationError) or getattr(getattr(e, "response", None), "status_code", None) == 401


def positive_int(text):
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def fail(message):
    print(f"tidal2bc: {message}", file=sys.stderr)
    return 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--login", action="store_true", help="log in to Tidal interactively and save the session")
    p.add_argument("--tracks-json", help="read [{artist, album, album_id}] or {tracks: [...], albums: [...]} from this file instead of Tidal")
    p.add_argument("--session", type=Path, default=CONFIG_DIR / "tidal-session.json")
    p.add_argument("--state", type=Path, default=CONFIG_DIR / "seen.json")
    p.add_argument("--out", type=Path, default=Path("out/albums.html"))
    p.add_argument("--min-favs", type=positive_int, default=2)
    args = p.parse_args(argv)

    os.umask(0o077)  # session and state files hold credentials
    session = tidalapi.Session()
    if args.login:
        args.session.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not session.login_session_file(args.session):
            return fail("Tidal login failed")
        print(f"Session saved to {args.session}")
        return 0

    try:
        seen = load_state(args.state)
    except ValueError as e:
        return fail(str(e))

    if args.tracks_json:
        try:
            tracks, fav_albums = load_tracks_json(args.tracks_json)
        except ValueError as e:
            return fail(str(e))
    else:
        try:
            tracks, fav_albums = fetch_favourites(session, args.session)
        except TidalError as e:
            return fail(str(e))
        except Exception as e:
            hint = ". Run: uv run tidal2bc.py --login" if is_auth_error(e) else ""
            return fail(f"could not read Tidal favourites: {e}{hint}")

    current = albums_to_track(tracks, args.min_favs, fav_albums)
    now = datetime.now(timezone.utc).isoformat()
    new = [a for a in current if a["id"] not in seen]
    seen.update({a["id"]: now for a in new})
    albums = sorted(current, key=lambda a: str(seen[a["id"]]), reverse=True)  # newest first, stable

    for path, text in [(args.state, json.dumps(seen, indent=1)), (args.out, render_page(albums))]:
        try:
            write_atomic(path, text)
        except OSError as e:
            return fail(f"could not write {path}: {e}")
    print(f"{len(new)} new album(s), {len(albums)} listed. Page: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
