# Stream To Bandcamp (stream2bc)

Finds the Tidal albums you favourited, or where you favourited at least two tracks, and helps you add each album to your Bandcamp wishlist.

The script lists the albums in a table on a web page, with a link to the album on Tidal and a Bandcamp search link for each one. A userscript in your own browser adds an album to your wishlist when you open it from that page.

## Set up

1. Log in to Tidal once. The command prints a link, which you open and approve:

   ```sh
   uv run stream2bc.py --login
   ```

2. Install `stream2bc.user.js` in Tampermonkey or Violentmonkey.

The Tidal session is stored in `~/.config/stream2bc/tidal-session.json`, readable only by you. It stays on your machine. Your Bandcamp login stays in your browser. The script never contacts Bandcamp.

## Find albums

```sh
uv run stream2bc.py
```

The script writes `out/albums.html`, which lists the albums that currently qualify. An album qualifies if you favourited the album, or if you favourited at least two of its tracks (change the limit with `--min-favs`). An album that no longer qualifies drops off the page. The script remembers when it first listed each album in `~/.config/stream2bc/seen.json`.

Each row shows:

- **Artist** and **Album**.
- **Favourite album**: "yes" if you favourited the album.
- **Favourite tracks**: how many of its tracks you favourited.
- **Score**: the favourite tracks, plus 3 if you favourited the album.
- **First seen**: the date the script first listed the album.
- **Tidal**: a link to the album on Tidal.
- **Bandcamp**: a **Search** link that looks for the album on Bandcamp.

The page opens with the highest score first. Albums with the same score are ordered by the date they were first seen, newest first, and then by artist. To sort by another column, click its heading. Click it again to reverse the order. The arrow in the heading shows the current sort.

To run it daily at 07:00 from cron:

```cron
0 7 * * * cd /path/to/stream2bc && uv run stream2bc.py
```

If the Tidal session has expired, the script exits with an error and does not wait for input. Run `--login` again.

## Add albums to your wishlist

1. Open `out/albums.html` in your browser and log in to Bandcamp.
2. Click a **Search** link. The Bandcamp search opens in a new tab.
3. Click the matching album.

The album is added to your wishlist when it opens. The button in the corner changes to "Wishlisted by stream2bc", or to "stream2bc: failed, see console".

Albums you open any other way are not added automatically. On those pages, click **Add to wishlist (stream2bc)** to add the album, and an alert says whether it worked. To turn the automatic adding off, choose "stream2bc: auto-wishlist on/off" in the userscript manager's menu. The same entry turns it back on.

First time: open the browser console, open an album from the list, then check the `stream2bc: resolved fields` line. The script reads the album id from a page key that is not confirmed yet. The console also shows the key names of the page data it can read your fan id from. If any field is missing, nothing is posted and the missing field is named.

## Tests

```sh
uv run --with pytest --with tidalapi==0.8.11 pytest
node --test stream2bc.user.test.js
```
