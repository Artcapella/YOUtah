const test = require("node:test");
const assert = require("node:assert/strict");
const { renderMarkdown, safeUrl } = require("../public/assets/guide-renderer.js");

test("an itinerary gets separate headings, paragraphs, emphasis, and lists", () => {
  const html = renderMarkdown("Sounds like a lovely day!\n\n## Morning · A little outside time\n\nStart with **a short walk**.\n\n- Bring water\n- Leave time for lunch\n\n## Evening\n\nCheck the local calendar.");
  assert.match(html, /<p>Sounds like a lovely day!<\/p><h3>Morning/);
  assert.match(html, /<strong>a short walk<\/strong>/);
  assert.match(html, /<ul><li>Bring water<\/li><li>Leave time for lunch<\/li><\/ul>/);
  assert.match(html, /<h3>Evening<\/h3><p>Check the local calendar\.<\/p>/);
});

test("citation links retain source labels even after filtering or URL deduplication", () => {
  const html = renderMarkdown("A trail [S4]. Then music [W2].", [
    { label: "S4", title: "A trail", url: "https://example.com/trail" },
    { label: "W2", title: "Music", url: "https://example.com/music" },
  ]);
  assert.match(html, /href="https:\/\/example.com\/trail"[^>]*>S4<\/a>/);
  assert.match(html, /href="https:\/\/example.com\/music"[^>]*>W2<\/a>/);
  assert.doesNotMatch(html, />S1<\/a>/);
});

test("HTML and injected attributes cannot become executable markup", () => {
  const html = renderMarkdown('<img src=x onerror=alert(1)> **Hello** [S1]', [
    { label: "S1", title: '\" onclick=\"alert(1)', url: "https://example.com/" },
  ]);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&quot; onclick=&quot;/);
  assert.doesNotMatch(html, /aria-label="[^"]*" onclick=/);
});

test("only supported links supplied with the answer become clickable", () => {
  const html = renderMarkdown("[Details](https://example.com/trail) [Made up](https://other.example/path) [S9]", [
    { label: "S1", title: "Trail", url: "https://example.com/trail" },
  ]);
  assert.match(html, /href="https:\/\/example.com\/trail"/);
  assert.doesNotMatch(html, /href="https:\/\/other.example/);
  assert.doesNotMatch(html, /\[S9\]/);
  assert.match(html, /Made up/);
});

test("unsafe or malformed URLs never become links", () => {
  for (const url of ["javascript:alert(1)", "data:text/html,hi", "/relative", "https://example.com/\n", "https://user:secret@example.com/"]) {
    assert.equal(safeUrl(url), null);
    assert.doesNotMatch(renderMarkdown("Try this [S1]", [{ label: "S1", title: "Bad", url }]), /href=/);
  }
});
