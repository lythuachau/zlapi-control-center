(() => {
  "use strict";

  const LINK_PATTERN = /(?:https?:\/\/|www\.)[^\s<>"'`]+/giu;
  const BLOCKED_PREFIX_PATTERN = /[\p{L}\p{N}_@.-]/u;
  const TRAILING_PUNCTUATION = new Set([
    ".", ",", "!", "?", ";", ":", "…", "，", "。", "！", "？", "；", "：",
  ]);
  const CLOSING_PAIRS = new Map([
    [")", "("],
    ["]", "["],
    ["}", "{"],
  ]);

  function appendTextToken(tokens, value) {
    if (!value) return;
    const previous = tokens.at(-1);
    if (previous?.type === "text") {
      previous.value += value;
      return;
    }
    tokens.push({ type: "text", value });
  }

  function characterCount(value, character) {
    let count = 0;
    for (const current of value) {
      if (current === character) count += 1;
    }
    return count;
  }

  function splitTrailingPunctuation(candidate) {
    let linkText = candidate;
    let suffix = "";
    let changed = true;

    while (linkText && changed) {
      changed = false;
      const lastCharacter = linkText.at(-1);
      if (TRAILING_PUNCTUATION.has(lastCharacter)) {
        linkText = linkText.slice(0, -1);
        suffix = `${lastCharacter}${suffix}`;
        changed = true;
        continue;
      }

      const openingCharacter = CLOSING_PAIRS.get(lastCharacter);
      if (
        openingCharacter
        && characterCount(linkText, lastCharacter) > characterCount(linkText, openingCharacter)
      ) {
        linkText = linkText.slice(0, -1);
        suffix = `${lastCharacter}${suffix}`;
        changed = true;
      }
    }

    return { linkText, suffix };
  }

  function normalizeHref(linkText) {
    const candidate = /^www\./iu.test(linkText) ? `https://${linkText}` : linkText;
    try {
      const parsed = new URL(candidate);
      if (!new Set(["http:", "https:"]).has(parsed.protocol)) return "";
      if (!parsed.hostname) return "";
      return parsed.href;
    } catch (_error) {
      return "";
    }
  }

  function tokenizeLinks(value) {
    const source = typeof value === "string" ? value : String(value ?? "");
    const tokens = [];
    let cursor = 0;

    LINK_PATTERN.lastIndex = 0;
    for (const match of source.matchAll(LINK_PATTERN)) {
      const matchIndex = match.index ?? 0;
      appendTextToken(tokens, source.slice(cursor, matchIndex));

      const candidate = match[0];
      const previousCharacter = matchIndex > 0 ? source[matchIndex - 1] : "";
      const { linkText, suffix } = splitTrailingPunctuation(candidate);
      const href = previousCharacter && BLOCKED_PREFIX_PATTERN.test(previousCharacter)
        ? ""
        : normalizeHref(linkText);

      if (!href) {
        appendTextToken(tokens, candidate);
      } else {
        let hostname = "";
        try {
          hostname = new URL(href).hostname;
        } catch (_error) {
          hostname = "";
        }
        tokens.push({
          type: "link",
          value: linkText,
          href,
          hostname,
        });
        appendTextToken(tokens, suffix);
      }
      cursor = matchIndex + candidate.length;
    }

    appendTextToken(tokens, source.slice(cursor));
    return tokens;
  }

  function appendLinkedText(container, value) {
    if (!container) return;
    const ownerDocument = container.ownerDocument || globalThis.document;
    if (!ownerDocument) {
      container.textContent = typeof value === "string" ? value : String(value ?? "");
      return;
    }

    for (const token of tokenizeLinks(value)) {
      if (token.type === "text") {
        container.append(ownerDocument.createTextNode(token.value));
        continue;
      }

      const link = ownerDocument.createElement("a");
      link.className = "zchat-message-link";
      link.textContent = token.value;
      link.href = token.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer nofollow ugc";
      link.referrerPolicy = "no-referrer";
      link.title = token.hostname
        ? `Mở liên kết bên ngoài: ${token.hostname}`
        : "Mở liên kết bên ngoài";
      container.append(link);
    }
  }

  const api = Object.freeze({ appendLinkedText, tokenizeLinks });
  if (typeof window !== "undefined") window.ZChatText = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
