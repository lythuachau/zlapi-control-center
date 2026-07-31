const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const projectRoot = path.resolve(__dirname, "../..");
const helperPath = path.join(
  projectRoot,
  "control_center",
  "static",
  "js",
  "chat-text.js",
);

class FakeNode {
  constructor(ownerDocument, nodeType) {
    this.ownerDocument = ownerDocument;
    this.nodeType = nodeType;
    this.childNodes = [];
    this.parentNode = null;
  }

  append(...nodes) {
    for (const value of nodes) {
      const node = value instanceof FakeNode
        ? value
        : this.ownerDocument.createTextNode(String(value));
      if (node.nodeType === 11) {
        this.append(...node.childNodes);
        node.childNodes = [];
        continue;
      }
      node.parentNode = this;
      this.childNodes.push(node);
    }
  }

  appendChild(node) {
    this.append(node);
    return node;
  }

  replaceChildren(...nodes) {
    for (const child of this.childNodes) child.parentNode = null;
    this.childNodes = [];
    this.append(...nodes);
  }

  get textContent() {
    return this.childNodes.map((node) => node.textContent).join("");
  }

  set textContent(value) {
    this.replaceChildren(this.ownerDocument.createTextNode(String(value ?? "")));
  }
}

class FakeText extends FakeNode {
  constructor(ownerDocument, value) {
    super(ownerDocument, 3);
    this.data = String(value);
  }

  get textContent() {
    return this.data;
  }

  set textContent(value) {
    this.data = String(value ?? "");
  }
}

class FakeElement extends FakeNode {
  constructor(ownerDocument, tagName) {
    super(ownerDocument, 1);
    this.tagName = String(tagName).toUpperCase();
    this.attributes = new Map();
    this._classes = new Set();
    this.classList = {
      add: (...names) => {
        for (const name of names) this._classes.add(name);
        this.attributes.set("class", [...this._classes].join(" "));
      },
      contains: (name) => this._classes.has(name),
    };
  }

  setAttribute(name, value) {
    this.attributes.set(String(name), String(value));
  }

  getAttribute(name) {
    return this.attributes.get(String(name)) ?? null;
  }

  set className(value) {
    this._classes = new Set(String(value).split(/\s+/).filter(Boolean));
    this.attributes.set("class", [...this._classes].join(" "));
  }

  get className() {
    return this.attributes.get("class") ?? "";
  }

  set href(value) {
    this.setAttribute("href", value);
  }

  get href() {
    return this.getAttribute("href") ?? "";
  }

  set target(value) {
    this.setAttribute("target", value);
  }

  get target() {
    return this.getAttribute("target") ?? "";
  }

  set rel(value) {
    this.setAttribute("rel", value);
  }

  get rel() {
    return this.getAttribute("rel") ?? "";
  }

  set innerHTML(_value) {
    throw new Error("appendLinkedText must not render user input with innerHTML");
  }
}

class FakeDocument {
  createElement(tagName) {
    return new FakeElement(this, tagName);
  }

  createTextNode(value) {
    return new FakeText(this, value);
  }

  createDocumentFragment() {
    return new FakeNode(this, 11);
  }
}

const fakeDocument = new FakeDocument();
global.document = fakeDocument;

const chatText = require(helperPath);

function walkElements(node) {
  return node.childNodes.flatMap((child) => [
    ...(child.nodeType === 1 ? [child] : []),
    ...walkElements(child),
  ]);
}

function render(value) {
  const container = fakeDocument.createElement("div");
  chatText.appendLinkedText(container, value);
  return {
    container,
    anchors: walkElements(container).filter((node) => node.tagName === "A"),
  };
}

function assertSafeAnchor(anchor) {
  assert.match(anchor.href, /^https?:\/\//i);
  assert.equal(anchor.target, "_blank");
  const rel = new Set(anchor.rel.split(/\s+/).filter(Boolean));
  assert.ok(rel.has("noopener"));
  assert.ok(rel.has("noreferrer"));
  assert.ok(rel.has("nofollow"));
  assert.ok(anchor.classList.contains("zchat-message-link"));
}

test("exports the tokenizer and safe DOM renderer", () => {
  assert.equal(typeof chatText.tokenizeLinks, "function");
  assert.equal(typeof chatText.appendLinkedText, "function");
  assert.ok(Array.isArray(chatText.tokenizeLinks("Không có liên kết")));
});

test("linkifies http, https, and www URLs with safe anchor attributes", () => {
  const input = [
    "HTTP http://plain.example/a,",
    "HTTPS https://secure.example/b?x=1#top;",
    "WWW www.example.net/docs.",
  ].join(" ");
  const { container, anchors } = render(input);

  assert.equal(container.textContent, input);
  assert.deepEqual(
    anchors.map((anchor) => anchor.textContent),
    [
      "http://plain.example/a",
      "https://secure.example/b?x=1#top",
      "www.example.net/docs",
    ],
  );
  assert.deepEqual(
    anchors.map((anchor) => anchor.href),
    [
      "http://plain.example/a",
      "https://secure.example/b?x=1#top",
      "https://www.example.net/docs",
    ],
  );
  anchors.forEach(assertSafeAnchor);
});

test("preserves newlines and blank lines around multiple links", () => {
  const input = [
    "Dòng đầu https://one.example/path",
    "",
    "Dòng cuối có www.two.example/docs và http://three.example.",
  ].join("\n");
  const { container, anchors } = render(input);

  assert.equal(container.textContent, input);
  assert.equal(anchors.length, 3);
  anchors.forEach(assertSafeAnchor);
});

test("excludes sentence punctuation while retaining balanced URL parentheses", () => {
  const input = [
    "Ngoài ngoặc (https://example.com/docs).",
    "Cân bằng https://example.com/a_(b).",
    "Thừa ngoặc https://example.com/a(b))!",
  ].join(" ");
  const { container, anchors } = render(input);

  assert.equal(container.textContent, input);
  assert.deepEqual(
    anchors.map((anchor) => anchor.textContent),
    [
      "https://example.com/docs",
      "https://example.com/a_(b)",
      "https://example.com/a(b)",
    ],
  );
});

test("does not turn javascript, data, or other protocols into links", () => {
  const input = [
    "javascript:alert(1)",
    "data:text/html,<svg/onload=alert(1)>",
    "ftp://files.example/archive.zip",
    "mailto:admin@example.com",
  ].join(" ");
  const { container, anchors } = render(input);

  assert.equal(container.textContent, input);
  assert.deepEqual(anchors, []);
});

test("keeps HTML and XSS-looking input as text nodes", () => {
  const input = [
    '<img src=x onerror="globalThis.pwned=true">',
    '<script>globalThis.pwned=true</script>',
    "https://safe.example/path",
  ].join("\n");
  const { container, anchors } = render(input);
  const elements = walkElements(container);

  assert.equal(container.textContent, input);
  assert.equal(globalThis.pwned, undefined);
  assert.equal(anchors.length, 1);
  assert.deepEqual(
    elements.map((node) => node.tagName),
    ["A"],
  );
  assertSafeAnchor(anchors[0]);
});

test("integrates linkified full messages while keeping list and quote previews compact", () => {
  const chatJs = fs.readFileSync(
    path.join(projectRoot, "control_center", "static", "js", "chat.js"),
    "utf8",
  );
  const chatCss = fs.readFileSync(
    path.join(projectRoot, "control_center", "static", "css", "chat.css"),
    "utf8",
  );
  const chatTemplate = fs.readFileSync(
    path.join(projectRoot, "control_center", "templates", "chat.html"),
    "utf8",
  );

  const messageBody = chatJs.slice(
    chatJs.indexOf("function renderMessageBody"),
    chatJs.indexOf("function renderQuotedMessage"),
  );
  assert.match(messageBody, /classList\.add\(["']has-text["']\)/);
  assert.match(messageBody, /appendLinkedText\(bubble,\s*message\.text\)/);

  const conversationPreview = chatJs.slice(
    chatJs.indexOf('preview.className = "zchat-conversation-preview"'),
    chatJs.indexOf('preview.className = "zchat-conversation-preview"') + 500,
  );
  assert.match(conversationPreview, /preview\.textContent\s*=/);
  assert.doesNotMatch(conversationPreview, /appendLinkedText/);

  const quoteRenderer = chatJs.slice(
    chatJs.indexOf("function renderQuotedMessage"),
    chatJs.indexOf("function renderReactionSummary"),
  );
  assert.match(quoteRenderer, /copy\.textContent\s*=\s*compactText\(/);
  assert.doesNotMatch(quoteRenderer, /appendLinkedText/);
  assert.match(chatJs, /replace\(\/\\s\+\/g,\s*["'] ["']\)/);

  assert.match(
    chatCss,
    /\.zchat-message-bubble\.has-text[\s\S]{0,240}white-space:\s*pre-wrap\s*;/,
  );
  assert.match(
    chatCss,
    /\.zchat-message-bubble\.has-text[\s\S]{0,300}overflow-wrap:\s*anywhere\s*;/,
  );
  assert.match(
    chatCss,
    /\.zchat-conversation-preview\s*\{[^}]*white-space:\s*nowrap\s*;/s,
  );
  assert.match(
    chatCss,
    /\.zchat-message-quote[^{}]*span[^{}]*\{[^}]*white-space:\s*nowrap\s*;/s,
  );
  assert.match(chatCss, /\.zchat-message-link\s*\{/);

  const helperScript = chatTemplate.indexOf("/js/chat-text.js");
  const chatScript = chatTemplate.indexOf("/js/chat.js");
  assert.ok(helperScript >= 0, "chat.html must load chat-text.js");
  assert.ok(chatScript > helperScript, "chat-text.js must load before chat.js");
});
