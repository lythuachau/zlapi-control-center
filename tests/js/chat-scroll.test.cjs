const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const projectRoot = path.resolve(__dirname, "../..");
const chatPath = path.join(
  projectRoot,
  "control_center",
  "static",
  "js",
  "chat.js",
);
const source = fs.readFileSync(chatPath, "utf8").replace(/\r\n?/g, "\n");

function functionSource(name) {
  const signature = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\(`);
  const match = signature.exec(source);
  assert.ok(match, `Khong tim thay ham ${name} trong chat.js`);

  const openingParen = source.indexOf("(", match.index);
  let parameterDepth = 0;
  let closingParen = -1;
  let parameterQuote = "";
  let parameterEscaped = false;
  for (let index = openingParen; index < source.length; index += 1) {
    const character = source[index];
    if (parameterQuote) {
      if (parameterEscaped) parameterEscaped = false;
      else if (character === "\\") parameterEscaped = true;
      else if (character === parameterQuote) parameterQuote = "";
      continue;
    }
    if (character === "\"" || character === "'" || character === "`") {
      parameterQuote = character;
      continue;
    }
    if (character === "(") parameterDepth += 1;
    if (character === ")") {
      parameterDepth -= 1;
      if (parameterDepth === 0) {
        closingParen = index;
        break;
      }
    }
  }
  assert.notEqual(closingParen, -1, `Ham ${name} khong dong danh sach tham so`);

  const openingBrace = source.indexOf("{", closingParen + 1);
  assert.notEqual(openingBrace, -1, `Ham ${name} khong co than ham`);

  let depth = 0;
  let quote = "";
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (let index = openingBrace; index < source.length; index += 1) {
    const character = source[index];
    const next = source[index + 1] || "";

    if (lineComment) {
      if (character === "\n") lineComment = false;
      continue;
    }
    if (blockComment) {
      if (character === "*" && next === "/") {
        blockComment = false;
        index += 1;
      }
      continue;
    }
    if (quote) {
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === quote) {
        quote = "";
      }
      continue;
    }
    if (character === "/" && next === "/") {
      lineComment = true;
      index += 1;
      continue;
    }
    if (character === "/" && next === "*") {
      blockComment = true;
      index += 1;
      continue;
    }
    if (character === "\"" || character === "'" || character === "`") {
      quote = character;
      continue;
    }
    if (character === "{") depth += 1;
    if (character === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(match.index, index + 1);
    }
  }

  assert.fail(`Khong tim thay dau ket thuc cua ham ${name}`);
}

function sourceBetween(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  assert.notEqual(start, -1, `Khong tim thay moc bat dau: ${startMarker}`);
  const end = source.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(end, -1, `Khong tim thay moc ket thuc: ${endMarker}`);
  return source.slice(start, end);
}

test("message navigation co state va cac helper vong doi rieng", () => {
  const stateBlock = sourceBetween("const state = {", "\n  };\n");
  assert.match(
    stateBlock,
    /messageNavigation\s*:/,
    "state phai co messageNavigation de nhan dien dieu huong dang chay",
  );

  const active = functionSource("messageNavigationActive");
  assert.match(active, /state\.messageNavigation/);

  const cancel = functionSource("cancelMessageNavigation");
  assert.match(cancel, /state\.messageNavigation/);
  assert.match(cancel, /releaseNativeMessageAnchoring\s*\(\s*\)/);
  const release = functionSource("releaseNativeMessageAnchoring");
  assert.match(
    release,
    /is-positioning/,
    "huy dieu huong phai don class khoa scroll anchoring",
  );
});

test("auto load older khong chen ngang khi dang dieu huong den tin chua doc", () => {
  const scrollWork = functionSource("scheduleMessageScrollWork");
  const directGuard = /if\s*\([^)]*currentScrollTop\s*<=\s*90[^)]*!messageNavigationActive\(\)[^)]*\)\s*(?:\{\s*)?(?:void\s+)?loadOlderMessages\(\)/s;
  const cachedGuard = /(?:const|let)\s+(\w+)\s*=\s*messageNavigationActive\(\)[\s\S]*if\s*\([^)]*currentScrollTop\s*<=\s*90[^)]*!\1[^)]*\)\s*(?:\{\s*)?(?:void\s+)?loadOlderMessages\(\)/s;
  assert.ok(
    directGuard.test(scrollWork) || cachedGuard.test(scrollWork),
    "scroll len dau chi duoc load older khi message navigation da ket thuc",
  );
});

test("loadOlderMessages bo response neu navigation bat dau trong luc cho API", () => {
  const loadOlder = functionSource("loadOlderMessages");
  const awaitApi = loadOlder.indexOf("await api(");
  assert.ok(awaitApi > 0, "loadOlderMessages phai cho API tin cu");

  const guardBeforeRequest = loadOlder.indexOf("messageNavigationActive()");
  assert.ok(
    guardBeforeRequest > 0 && guardBeforeRequest < awaitApi,
    "khong khoi dong request tin cu khi navigation da active",
  );

  const guardAfterRequest = loadOlder.indexOf("messageNavigationActive()", awaitApi);
  assert.ok(
    guardAfterRequest > awaitApi,
    "response tin cu phai duoc bo neu navigation bat dau trong luc await",
  );

  const anchorAfterRequest = loadOlder.indexOf("captureScrollAnchor()", awaitApi);
  assert.ok(
    anchorAfterRequest > guardAfterRequest,
    "scroll anchor chi duoc chup sau API va sau khi xac nhan navigation khong active",
  );

  const returnAfterGuard = loadOlder.indexOf("return", guardAfterRequest);
  assert.ok(
    returnAfterGuard > guardAfterRequest && returnAfterGuard < anchorAfterRequest,
    "guard response stale phai return truoc khi chup/restore anchor",
  );
});

test("navigateToMessageEvent query target trong RAF va hieu chinh bang auto", () => {
  const navigate = functionSource("navigateToMessageEvent");
  const firstFrame = navigate.indexOf("requestAnimationFrame");
  const firstTargetQuery = navigate.indexOf("messageEventTarget(");
  assert.ok(firstFrame > 0, "dieu huong phai doi den animation frame");
  assert.ok(
    firstTargetQuery > firstFrame,
    "target phai duoc query trong RAF, khong giu DOM node cu truoc RAF",
  );

  const targetQueries = navigate.match(/messageEventTarget\s*\(/g) || [];
  assert.ok(
    targetQueries.length >= 2,
    "phai query lai target khi hieu chinh sau thay doi layout/media",
  );
  assert.match(
    navigate,
    /scrollIntoView\s*\(\s*\{[^}]*behavior\s*:\s*["']auto["']/s,
    "lan hieu chinh cuoi phai dung behavior auto de tranh giat lan hai",
  );
  assert.match(navigate, /beginMessageNavigation\s*\(/);
  const begin = functionSource("beginMessageNavigation");
  assert.match(begin, /suppressNativeMessageAnchoring\s*\(\s*\)/);
  const suppress = functionSource("suppressNativeMessageAnchoring");
  assert.match(suppress, /is-positioning/);
});

test("render unread dung navigation helper thay vi giu target DOM cu", () => {
  const render = functionSource("renderMessages");
  const unreadBranch = render.slice(
    render.indexOf('mode === "unread"'),
    render.indexOf('mode === "target"'),
  );
  assert.match(
    unreadBranch,
    /navigateToMessageEvent\s*\(\s*firstUnreadEventId/,
    "nhanh unread phai di qua helper co correction",
  );
  assert.doesNotMatch(
    unreadBranch,
    /querySelector|scrollIntoView/,
    "nhanh unread khong duoc query/scroll target truc tiep ngoai helper",
  );
});

test("xoa thread huy message navigation dang cho", () => {
  const clear = functionSource("clearActiveThread");
  assert.match(
    clear,
    /cancelMessageNavigation\s*\(/,
    "doi/clear thread phai huy RAF, timer va token cua navigation cu",
  );
});

test("realtime merge tu quyet dinh giu vi tri, append khong ep xuong day", () => {
  const append = functionSource("appendRealtimeMessage");
  assert.match(append, /mergeIncrementalMessages\s*\(\s*\[message\]\s*\)/);
  assert.doesNotMatch(
    append,
    /scrollMessages\s*\(/,
    "appendRealtimeMessage khong duoc pha anchor/navigation sau merge",
  );
});

test("nut xem tin chua doc cung su dung navigation helper", () => {
  const handler = sourceBetween(
    'unreadJump?.addEventListener("click"',
    "\n  for (const reactionList",
  );
  assert.match(handler, /navigateToMessageEvent\s*\(/);
  assert.doesNotMatch(
    handler,
    /scrollIntoView\s*\(/,
    "ke ca target da render, nut unread van phai bat navigation guard",
  );
});
