// ==UserScript==
// @name         tidal2bc: add to wishlist
// @namespace    tidal2bc
// @version      0.3
// @description  Wishlists albums opened from your tidal2bc list, and adds a manual button to album pages.
// @match        https://*.bandcamp.com/album/*
// @match        https://bandcamp.com/search*
// @grant        unsafeWindow
// @grant        GM_registerMenuCommand
// @grant        GM_getValue
// @grant        GM_setValue
// @run-at       document-idle
// ==/UserScript==

// With any @grant the script runs in a sandbox, so the page's own FanControls and TralbumData are read via unsafeWindow.
const MARKER = "#tidal2bc";
const AUTO_KEY = "autoWishlist";

const NO_DOC = { querySelector: () => null, getElementById: () => null };

function readData(el, key) {
  try {
    return el ? JSON.parse(el.dataset[key]) : undefined;
  } catch (e) {
    return undefined;
  }
}

const pageBlob = (doc) => readData(doc.getElementById("pagedata"), "blob");

// Payload shape follows sabjorn/BandcampEnhancementSuite (src/wishlist.ts): item_type "a" is an album.
// The album id key is unconfirmed: check the console output on first use.
function resolveFields(win) {
  const doc = win.document || NO_DOC;
  const td = win.TralbumData || readData(doc.querySelector("[data-tralbum]"), "tralbum") || {};
  const current = td.current || {};
  const band = readData(doc.querySelector("[data-band]"), "band") || {};
  const blob = pageBlob(doc) || {};
  return {
    fan_id: win.FanControls?.fan_id || blob.fan_data?.fan_id || blob.identities?.fan?.id || blob.fan_id,
    band_id: current.band_id || td.band_id || band.id,
    item_id: current.id || td.id,
    item_type: "a",
  };
}

async function fetchPost(win, fields, crumb) {
  const res = await win.fetch("/collect_item_cb", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest" },
    body: new URLSearchParams(crumb ? { ...fields, crumb } : fields),
  });
  return res.json();
}

async function postWishlist(win, doc, fields) {
  if (typeof win.FanControls?.doPost === "function") {
    return win.FanControls.doPost("/collect_item_cb", fields);
  }
  const crumb = readData(doc.getElementById("js-crumbs-data"), "crumbs")?.collect_item_cb;
  const res = await fetchPost(win, fields, crumb);
  return res.error === "invalid_crumb" && res.crumb ? fetchPost(win, fields, res.crumb) : res;
}

const isOk = (res) => Boolean(res) && res.ok !== false && !res.error;

async function addToWishlist(win, button, notify = (ok, message) => win.alert(message)) {
  button.disabled = true;
  try {
    const doc = win.document || NO_DOC;
    const fields = resolveFields(win);
    console.log("tidal2bc: resolved fields", fields);
    console.log("tidal2bc: page item_type", win.TralbumData?.item_type,
      "| crumb element present", !!doc.getElementById("js-crumbs-data"),
      "| pagedata blob keys", Object.keys(pageBlob(doc) || {}));
    const missing = Object.keys(fields).filter((k) => !fields[k]);
    if (missing.length) {
      notify(false, "tidal2bc: could not find " + missing.join(", ") + ". Nothing was posted.");
      return;
    }
    const res = await postWishlist(win, doc, fields);
    console.log("tidal2bc: response", res);
    notify(isOk(res), isOk(res) ? "tidal2bc: added to wishlist" : "tidal2bc: failed (see console)");
  } catch (e) {
    console.error("tidal2bc: request threw", e);
    notify(false, "tidal2bc: failed (see console)");
  } finally {
    button.disabled = false;
  }
}

function tagSearchLinks(doc) {
  for (const a of doc.querySelectorAll("a[href]")) {
    if (new URL(a.href).pathname.startsWith("/album/") && !a.href.endsWith(MARKER)) a.href += MARKER;
  }
}

function watchSearchPage(win) {
  if (win.location.hash !== MARKER) return;
  const tag = () => tagSearchLinks(win.document);
  tag();
  new win.MutationObserver(tag).observe(win.document.body, { childList: true, subtree: true });
}

const isAutoOn = (gm) => gm.getValue(AUTO_KEY, true);

function toggleAuto(gm) {
  const on = !isAutoOn(gm);
  gm.setValue(AUTO_KEY, on);
  return on;
}

// win is the sandbox window (location, history); page is the page's own window (see unsafeWindow above).
async function autoWishlist(win, page, button, enabled) {
  if (win.location.hash !== MARKER) return;
  win.history.replaceState(null, "", win.location.pathname + win.location.search); // a reload must not add again
  if (!enabled) return;
  await addToWishlist(page, button, (ok, message) => {
    if (!ok) console.error(message);
    button.textContent = ok ? "Wishlisted by tidal2bc" : "tidal2bc: failed, see console";
  });
}

if (typeof document === "undefined") {
  module.exports = { resolveFields, addToWishlist, tagSearchLinks, watchSearchPage, autoWishlist, isAutoOn, toggleAuto }; // node test only
} else {
  const gm = { getValue: GM_getValue, setValue: GM_setValue };
  GM_registerMenuCommand("tidal2bc: auto-wishlist on/off", () => {
    alert("tidal2bc: auto-wishlist is now " + (toggleAuto(gm) ? "ON" : "OFF"));
  });
  if (location.pathname.startsWith("/album/")) {
    const page = typeof unsafeWindow === "undefined" ? window : unsafeWindow;
    const button = document.createElement("button");
    button.textContent = "Add to wishlist (tidal2bc)";
    button.style.cssText = "position:fixed;bottom:16px;right:16px;z-index:99999;padding:8px 12px;cursor:pointer";
    button.addEventListener("click", () => addToWishlist(page, button));
    document.body.appendChild(button);
    autoWishlist(window, page, button, isAutoOn(gm));
  } else {
    watchSearchPage(window);
  }
}
