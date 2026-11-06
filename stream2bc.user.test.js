const test = require("node:test");
const assert = require("node:assert");
const { resolveFields, addToWishlist, tagSearchLinks, watchSearchPage, autoWishlist, isAutoOn, toggleAuto } =
  require("./stream2bc.user.js");

const logs = [];
console.log = (...args) => logs.push(args);
console.error = () => {};

const el = (data) => ({ dataset: data });
const json = (o) => JSON.stringify(o);

function makeWin({ els = {}, ...rest } = {}) {
  return {
    alerts: [],
    alert(m) { this.alerts.push(m); },
    document: { querySelector: (s) => els[s], getElementById: (id) => els["#" + id] },
    ...rest,
  };
}

const TD = { item_type: "album", current: { id: 3, band_id: 2 } };
const FIELDS = { fan_id: 1, band_id: 2, item_id: 3, item_type: "a" };

test("resolves fields from the primary keys", () => {
  assert.deepStrictEqual(resolveFields({ FanControls: { fan_id: 1 }, TralbumData: TD }), FIELDS);
});

test("falls back to TralbumData.id and TralbumData.band_id", () => {
  const win = { FanControls: { fan_id: 1 }, TralbumData: { id: 3, band_id: 2 } };
  assert.deepStrictEqual(resolveFields(win), FIELDS);
});

test("reports missing fields as falsy and tolerates an empty window; item_type is always a", () => {
  const r = resolveFields({});
  assert.deepStrictEqual(Object.keys(r).filter((k) => !r[k]), ["fan_id", "band_id", "item_id"]);
  assert.strictEqual(r.item_type, "a");
});

test("reads album data from data-tralbum when TralbumData is missing", () => {
  const els = { "[data-tralbum]": el({ tralbum: json(TD) }) };
  assert.deepStrictEqual(resolveFields(makeWin({ els, FanControls: { fan_id: 1 } })), FIELDS);
});

test("ignores malformed data-tralbum JSON", () => {
  const els = { "[data-tralbum]": el({ tralbum: "{nope" }) };
  const r = resolveFields(makeWin({ els, FanControls: { fan_id: 1 } }));
  assert.strictEqual(r.item_id, undefined);
});

test("falls back to data-band for band_id", () => {
  const els = {
    "[data-tralbum]": el({ tralbum: json({ current: { id: 3 } }) }),
    "[data-band]": el({ band: json({ id: 2 }) }),
  };
  assert.deepStrictEqual(resolveFields(makeWin({ els, FanControls: { fan_id: 1 } })), FIELDS);
});

test("falls back to the pagedata blob for fan_id, in order", () => {
  const fanId = (blob) => resolveFields(makeWin({ els: { "#pagedata": el({ blob: json(blob) }) }, TralbumData: TD })).fan_id;
  assert.strictEqual(fanId({ fan_data: { fan_id: 11 }, identities: { fan: { id: 12 } }, fan_id: 13 }), 11);
  assert.strictEqual(fanId({ identities: { fan: { id: 12 } }, fan_id: 13 }), 12);
  assert.strictEqual(fanId({ fan_id: 13 }), 13);
  assert.strictEqual(fanId({}), undefined);
});

test("FanControls.fan_id wins over the pagedata blob", () => {
  const els = { "#pagedata": el({ blob: json({ fan_id: 13 }) }) };
  assert.strictEqual(resolveFields(makeWin({ els, FanControls: { fan_id: 1 }, TralbumData: TD })).fan_id, 1);
});

test("posts with FanControls.doPost when it exists, and logs blob key names only", async () => {
  const calls = [];
  const els = { "#pagedata": el({ blob: json({ fan_data: { secret: "x" }, identities: {} }) }) };
  const win = makeWin({
    els,
    TralbumData: TD,
    FanControls: { fan_id: 1, doPost: async (...a) => { calls.push(a); return { ok: true }; } },
    fetch: () => assert.fail("fetch must not be used"),
  });
  logs.length = 0;
  await addToWishlist(win, { disabled: false });
  assert.deepStrictEqual(calls, [["/collect_item_cb", FIELDS]]);
  assert.match(win.alerts[0], /added/);
  assert.ok(logs.some((a) => a.some((x) => Array.isArray(x) && x.join() === "fan_data,identities")));
  assert.ok(!JSON.stringify(logs).includes("secret"));
});

function fetchWin(responses, crumbs = json({ collect_item_cb: "OLD" })) {
  const calls = [];
  const els = crumbs ? { "#js-crumbs-data": el({ crumbs }) } : {};
  const win = makeWin({
    els,
    TralbumData: TD,
    FanControls: { fan_id: 1 },
    fetch: async (url, opts) => {
      calls.push({ url, opts });
      const body = responses.shift();
      return { json: async () => body };
    },
  });
  return { win, calls };
}

const bodyOf = (call) => Object.fromEntries(new URLSearchParams(call.opts.body));

test("falls back to fetch with the crumb and retries once on invalid_crumb", async () => {
  const { win, calls } = fetchWin([{ error: "invalid_crumb", crumb: "NEW" }, { ok: true }]);
  await addToWishlist(win, { disabled: false });
  assert.strictEqual(calls.length, 2);
  assert.strictEqual(calls[0].url, "/collect_item_cb");
  assert.strictEqual(calls[0].opts.method, "POST");
  assert.strictEqual(calls[0].opts.credentials, "include");
  assert.deepStrictEqual(calls[0].opts.headers, {
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Requested-With": "XMLHttpRequest",
  });
  assert.deepStrictEqual(bodyOf(calls[0]), { fan_id: "1", item_id: "3", item_type: "a", band_id: "2", crumb: "OLD" });
  assert.strictEqual(bodyOf(calls[1]).crumb, "NEW");
  assert.match(win.alerts[0], /added/);
});

test("does not retry twice, and does not retry other errors", async () => {
  let r = fetchWin([{ error: "invalid_crumb", crumb: "A" }, { error: "invalid_crumb", crumb: "B" }, { ok: true }]);
  await addToWishlist(r.win, { disabled: false });
  assert.strictEqual(r.calls.length, 2);
  assert.match(r.win.alerts[0], /failed/);

  r = fetchWin([{ error: "nope" }, { ok: true }]);
  await addToWishlist(r.win, { disabled: false });
  assert.strictEqual(r.calls.length, 1);
  assert.match(r.win.alerts[0], /failed/);
});

test("fetch fallback omits the crumb when the page has none", async () => {
  const { win, calls } = fetchWin([{ ok: true }], null);
  await addToWishlist(win, { disabled: false });
  assert.ok(!("crumb" in bodyOf(calls[0])));
});

test("names missing fields in an alert and posts nothing", async () => {
  const win = makeWin({ fetch: () => assert.fail("no post"), FanControls: { doPost: () => assert.fail("no post") } });
  const button = { disabled: false };
  await addToWishlist(win, button);
  assert.match(win.alerts[0], /fan_id, band_id, item_id/);
  assert.strictEqual(button.disabled, false);
});

test("disables the button while the request runs and re-enables it after", async () => {
  let release;
  const win = makeWin({
    TralbumData: TD,
    FanControls: { fan_id: 1, doPost: () => new Promise((resolve) => { release = resolve; }) },
  });
  const button = { disabled: false };
  const done = addToWishlist(win, button);
  assert.strictEqual(button.disabled, true);
  release({ ok: true });
  await done;
  assert.strictEqual(button.disabled, false);

  win.FanControls.doPost = async () => { throw new Error("boom"); };
  await addToWishlist(win, button);
  assert.strictEqual(button.disabled, false);
  assert.match(win.alerts.at(-1), /failed/);
});

const searchDoc = (links) => ({ body: {}, querySelectorAll: () => links });

test("tags album links on a search page once, and leaves other links alone", () => {
  const links = [
    { href: "https://a.bandcamp.com/album/x?from=search" },
    { href: "https://a.bandcamp.com/track/y?from=search" },
    { href: "https://bandcamp.com/search?q=z" },
    { href: "https://b.bandcamp.com/album/w#stream2bc" },
  ];
  tagSearchLinks(searchDoc(links));
  tagSearchLinks(searchDoc(links));
  assert.deepStrictEqual(links.map((l) => l.href), [
    "https://a.bandcamp.com/album/x?from=search#stream2bc",
    "https://a.bandcamp.com/track/y?from=search",
    "https://bandcamp.com/search?q=z",
    "https://b.bandcamp.com/album/w#stream2bc",
  ]);
});

test("search page: tags only with the marker, and tags links that render later", () => {
  const observers = [];
  class FakeObserver {
    constructor(callback) { observers.push(callback); }
    observe(target, options) { this.options = options; }
  }
  const links = [{ href: "https://a.bandcamp.com/album/x" }];
  const win = { location: { hash: "" }, document: searchDoc(links), MutationObserver: FakeObserver };
  watchSearchPage(win);
  assert.strictEqual(links[0].href, "https://a.bandcamp.com/album/x");
  assert.strictEqual(observers.length, 0);

  win.location.hash = "#stream2bc";
  watchSearchPage(win);
  assert.strictEqual(links[0].href, "https://a.bandcamp.com/album/x#stream2bc");
  links.push({ href: "https://c.bandcamp.com/album/late" });
  observers[0]();
  observers[0]();
  assert.strictEqual(links[1].href, "https://c.bandcamp.com/album/late#stream2bc");
  assert.strictEqual(links[0].href, "https://a.bandcamp.com/album/x#stream2bc");
});

function albumPage(hash, response = { ok: true }) {
  const order = [];
  const sandbox = {
    location: { pathname: "/album/x", search: "?from=search", hash },
    history: { replaceState: (...a) => { order.push(["replaceState", ...a]); sandbox.location.hash = ""; } },
  };
  const page = makeWin({
    TralbumData: TD,
    FanControls: { fan_id: 1, doPost: async () => { order.push("post"); return response; } },
  });
  return { sandbox, page, order, button: { disabled: false, textContent: "Add to wishlist (stream2bc)" } };
}

test("album page with the marker clears the hash, then posts once and shows the result in the button", async () => {
  const { sandbox, page, order, button } = albumPage("#stream2bc");
  await autoWishlist(sandbox, page, button, true);
  assert.deepStrictEqual(order, [["replaceState", null, "", "/album/x?from=search"], "post"]);
  assert.strictEqual(button.textContent, "Wishlisted by stream2bc");
  assert.deepStrictEqual(page.alerts, []);
  await autoWishlist(sandbox, page, button, true);
  assert.strictEqual(order.filter((x) => x === "post").length, 1);
});

test("a failed automatic post is shown in the button label", async () => {
  const { sandbox, page, button } = albumPage("#stream2bc", { error: "nope" });
  await autoWishlist(sandbox, page, button, true);
  assert.strictEqual(button.textContent, "stream2bc: failed, see console");
  assert.deepStrictEqual(page.alerts, []);
});

test("album page without the marker does not post or touch the history", async () => {
  const { sandbox, page, order, button } = albumPage("");
  await autoWishlist(sandbox, page, button, true);
  assert.deepStrictEqual(order, []);
  assert.strictEqual(button.textContent, "Add to wishlist (stream2bc)");
});

test("auto-wishlist toggle defaults on, flips, and stops the automatic post when off", async () => {
  const store = {};
  const gm = { getValue: (k, d) => (k in store ? store[k] : d), setValue: (k, v) => { store[k] = v; } };
  assert.strictEqual(isAutoOn(gm), true);
  assert.strictEqual(toggleAuto(gm), false);
  const { sandbox, page, order, button } = albumPage("#stream2bc");
  await autoWishlist(sandbox, page, button, isAutoOn(gm));
  assert.deepStrictEqual(order, [["replaceState", null, "", "/album/x?from=search"]]);
  assert.strictEqual(button.textContent, "Add to wishlist (stream2bc)");
  assert.strictEqual(toggleAuto(gm), true);
});
