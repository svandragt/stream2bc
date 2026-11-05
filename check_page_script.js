// Runs the generated page's inline script in node:vm against a minimal fake table built from the page's HTML.
// Usage: node check_page_script.js out/albums.html (run by test_inline_script_sorts_the_table_in_node)
const assert = require("node:assert");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

const parseAttrs = (text) => new Map([...text.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map(([, k, v]) => [k, v ?? ""]));

function fakeHeader(attrText, inner) {
  const attrs = parseAttrs(attrText);
  const arrowText = inner.match(/<span class="arrow">(.*?)<\/span>/);
  const arrow = arrowText && { textContent: arrowText[1] };
  const clicks = [];
  const button = /<button/.test(inner) ? { addEventListener: (type, fn) => clicks.push(fn), click: () => clicks.forEach((fn) => fn()) } : null;
  return {
    label: inner.replace(/<[^>]+>|[▲▼]/g, "").trim(), button, arrow,
    getAttribute: (k) => (attrs.has(k) ? attrs.get(k) : null),
    setAttribute: (k, v) => attrs.set(k, String(v)),
    removeAttribute: (k) => attrs.delete(k),
    hasAttribute: (k) => attrs.has(k),
    querySelector: (sel) => (sel === "button" ? button : sel === ".arrow" ? arrow : null),
  };
}

const heads = [...html.matchAll(/<th([^>]*)>(.*?)<\/th>/gs)].map(([, attrs, inner]) => fakeHeader(attrs, inner));
const tbody = {
  rows: [...html.match(/<tbody>([\s\S]*?)<\/tbody>/)[1].matchAll(/<tr>(.*?)<\/tr>/gs)].map(([, row]) => {
    const cells = [...row.matchAll(/<td(?: data-sort="(.*?)")?>(.*?)<\/td>/gs)].map(([, sort, text]) => ({
      text, getAttribute: (k) => (k === "data-sort" ? sort ?? null : null),
    }));
    return { cells, artist: cells[0].text };
  }),
  appendChild(row) { this.rows = this.rows.filter((r) => r !== row).concat(row); },
};
const document = {
  querySelectorAll: (sel) => (sel === "thead th" ? heads : []),
  querySelector: (sel) => (sel === "tbody" ? tbody : null),
};
vm.runInNewContext(script, { document });

const head = (label) => heads.find((h) => h.label === label);
const order = () => tbody.rows.map((r) => r.artist);
const sorted = () => heads.filter((h) => h.hasAttribute("aria-sort")).map((h) => `${h.label} ${h.getAttribute("aria-sort")} ${h.arrow.textContent}`);

assert.deepStrictEqual(order(), ["b", "A", "c", "D"]);
assert.deepStrictEqual(sorted(), ["Score descending ▼"]);
assert.strictEqual(head("Tidal").button, null);
assert.strictEqual(head("Bandcamp").button, null);

head("Artist").button.click(); // text, case-insensitive, ascending first
assert.deepStrictEqual(order(), ["A", "b", "c", "D"]);
assert.deepStrictEqual(sorted(), ["Artist ascending ▲"]);
assert.strictEqual(head("Score").arrow.textContent, "");

head("Artist").button.click(); // same column again reverses
assert.deepStrictEqual(order(), ["D", "c", "b", "A"]);
assert.deepStrictEqual(sorted(), ["Artist descending ▼"]);

head("Favourite tracks").button.click(); // numeric (10 after 9), ties keep the current order (D before c)
assert.deepStrictEqual(order(), ["D", "c", "A", "b"]);
assert.deepStrictEqual(sorted(), ["Favourite tracks ascending ▲"]);

head("Favourite tracks").button.click(); // reversed, ties still keep the current order
assert.deepStrictEqual(order(), ["b", "A", "D", "c"]);
assert.deepStrictEqual(sorted(), ["Favourite tracks descending ▼"]);

head("Score").button.click();
assert.deepStrictEqual(order(), ["D", "c", "A", "b"]);
assert.deepStrictEqual(sorted(), ["Score ascending ▲"]);
console.log("ok");
