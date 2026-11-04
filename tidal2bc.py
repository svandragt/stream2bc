# /// script
# requires-python = ">=3.10"
# dependencies = ["tidalapi==0.8.11"]
# ///
"""List Tidal albums with at least N favourite tracks, as a page of Bandcamp search links."""
import argparse
import html
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote_plus

import tidalapi
from tidalapi.exceptions import AuthenticationError

CONFIG_DIR = Path.home() / ".config" / "tidal2bc"


def albums_to_track(tracks, min_favs=2):
    """Group favourite tracks by album id; keep albums with >= min_favs, most favourites first."""
    found = {}
    for t in tracks:
        if t.album is None or t.album.id is None or t.artist is None or not t.album.name or not t.artist.name:
            continue
        key = str(t.album.id)
        # shortcut: tidalapi fills album.artist from the track's own artists for favourites (media.py:247),
        # so this is the first favourited track's artist; wrong for compilations
        artist = getattr(getattr(t.album, "artist", None), "name", None) or t.artist.name
        entry = found.setdefault(key, {"artist": artist, "title": t.album.name, "id": key, "count": 0})
        entry["count"] += 1
    kept = [a for a in found.values() if a["count"] >= min_favs]
    return sorted(kept, key=lambda a: -a["count"])  # sorted() is stable


def bandcamp_search_url(album):
    # The hash is never sent to Bandcamp; the userscript reads it to know the page was opened from this list.
    return f"https://bandcamp.com/search?q={quote_plus(album['artist'] + ' ' + album['title'])}&item_type=a#tidal2bc"


def render_page(albums):
    items = "\n".join(
        f'<li><a href="{html.escape(bandcamp_search_url(a))}" target="_blank" rel="noopener">'
        f'{html.escape(a["artist"])} - {html.escape(a["title"])}</a> ({a["count"]} favourites)</li>'
        for a in albums
    )
    return (
        '<!doctype html>\n<meta charset="utf-8">\n<title>Tidal albums for Bandcamp</title>\n'
        f"<h1>Tidal albums for Bandcamp ({len(albums)})</h1>\n<ol>\n{items}\n</ol>\n"
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
    """Return fake tracks from [{artist, album, album_id}]; raise ValueError if the file is unusable."""
    try:
        rows = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read tracks file {path}: {e}")
    if not isinstance(rows, list):
        raise ValueError(f"tracks file {path} must contain a JSON list")
    tracks = []
    for i, r in enumerate(rows):
        try:
            tracks.append(SimpleNamespace(artist=SimpleNamespace(name=r["artist"]),
                                          album=SimpleNamespace(id=r["album_id"], name=r["album"])))
        except (KeyError, TypeError):
            raise ValueError(f"tracks file {path}: row {i} must be an object with artist, album and album_id")
    return tracks


class TidalError(Exception):
    """An expected Tidal failure whose message is shown as is."""


def fetch_favourite_tracks(session, session_path):
    if not session.load_session_from_file(session_path):
        raise TidalError(f"no Tidal session at {session_path}. Run: uv run tidal2bc.py --login")
    if not session.check_login():
        raise TidalError(f"Tidal session not valid (expired, or Tidal is unavailable). If this persists, run: uv run tidal2bc.py --login")
    favourites = session.user.favorites
    tracks = []
    # Not tracks_paginated: its worker (tidalapi/workers.py:func_wrapper) swallows page errors and returns fewer tracks.
    # Pages can be short (unavailable tracks), so the count only bounds the offsets.
    for offset in range(0, favourites.get_tracks_count(), 50):
        tracks.extend(favourites.tracks(limit=50, offset=offset))
    session.save_session_to_file(session_path)  # persists tokens tidalapi refreshed during the run
    return tracks


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
    p.add_argument("--tracks-json", help="read [{artist, album, album_id}] from this file instead of Tidal")
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
            tracks = load_tracks_json(args.tracks_json)
        except ValueError as e:
            return fail(str(e))
    else:
        try:
            tracks = fetch_favourite_tracks(session, args.session)
        except TidalError as e:
            return fail(str(e))
        except Exception as e:
            hint = ". Run: uv run tidal2bc.py --login" if is_auth_error(e) else ""
            return fail(f"could not read Tidal favourites: {e}{hint}")

    current = albums_to_track(tracks, args.min_favs)
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
