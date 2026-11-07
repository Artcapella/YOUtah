/* Small, dependency-free guide formatter. Raw HTML is always treated as text. */
(function (root, factory) {
  const renderer = factory();
  if (typeof module === "object" && module.exports) module.exports = renderer;
  else root.YouTahGuideRenderer = renderer;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[character]));
  }

  function safeUrl(value) {
    if (typeof value !== "string" || /[\s\u0000-\u001f\u007f]/.test(value)) return null;
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  function citationLabel(citation, index) {
    return /^[SW]\d+$/.test(citation.label || "") ? citation.label : `S${index + 1}`;
  }

  function inline(text, citations) {
    const byLabel = new Map(citations.map((citation, index) => [citationLabel(citation, index), citation]));
    const approvedUrls = new Set(citations.map((citation) => safeUrl(citation.url)).filter(Boolean));
    const token = /\*\*([^*\n]+)\*\*|\[([^\]\n]+)\]\((https?:\/\/[^\s]+?)\)|\[([SW]\d+)\]/g;
    let html = "";
    let offset = 0;
    for (const match of text.matchAll(token)) {
      html += escapeHtml(text.slice(offset, match.index));
      if (match[1]) {
        html += `<strong>${inline(match[1], citations)}</strong>`;
      } else if (match[2]) {
        const url = safeUrl(match[3]);
        const title = escapeHtml(match[2]);
        html += url && approvedUrls.has(url)
          ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${title}</a>`
          : title;
      } else {
        const citation = byLabel.get(match[4]);
        const url = citation && safeUrl(citation.url);
        html += url
          ? `<a class="guide-citation" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" aria-label="Source ${escapeHtml(match[4])}: ${escapeHtml(citation.title || citation.source_name || "Details")}">${escapeHtml(match[4])}</a>`
          : "";
      }
      offset = match.index + match[0].length;
    }
    return html + escapeHtml(text.slice(offset));
  }

  function renderMarkdown(value, citations = []) {
    const text = String(value ?? "").replace(/\r\n?/g, "\n");
    const html = [];
    let paragraph = [];
    let listType = null;
    function flushParagraph() {
      if (paragraph.length) html.push(`<p>${inline(paragraph.join(" "), citations)}</p>`);
      paragraph = [];
    }
    function closeList() {
      if (listType) html.push(`</${listType}>`);
      listType = null;
    }
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      const heading = line.match(/^#{1,6}\s+(.+)$/);
      const unordered = line.match(/^[-*•]\s+(.+)$/);
      const ordered = line.match(/^\d+[.)]\s+(.+)$/);
      if (!line) {
        flushParagraph();
        closeList();
      } else if (heading) {
        flushParagraph();
        closeList();
        html.push(`<h3>${inline(heading[1], citations)}</h3>`);
      } else if (unordered || ordered) {
        flushParagraph();
        const nextType = ordered ? "ol" : "ul";
        if (listType !== nextType) {
          closeList();
          html.push(`<${nextType}>`);
          listType = nextType;
        }
        html.push(`<li>${inline((ordered || unordered)[1], citations)}</li>`);
      } else {
        closeList();
        paragraph.push(line);
      }
    }
    flushParagraph();
    closeList();
    return html.join("");
  }

  return { renderMarkdown, safeUrl, citationLabel };
});
