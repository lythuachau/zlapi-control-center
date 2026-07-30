import assert from "node:assert/strict";
import { access, readFile, readdir } from "node:fs/promises";
import test from "node:test";

const projectRoot = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: {
        accept: "text/html",
        host: "localhost:3000",
        "x-forwarded-proto": "http",
      },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the complete theme selector", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>ZChat UI Lab/);
  assert.match(html, /ZCHAT DESK/);
  assert.match(html, /Midnight/);
  assert.match(html, /Daylight/);
  assert.match(html, /Signal Desk/);
  assert.match(html, /Tùy chỉnh theme/);
  assert.match(html, /THEME ĐANG DÙNG/);
  assert.match(html, /dữ liệu mẫu/);
  assert.doesNotMatch(html, /codex-preview|Building your site|react-loading-skeleton/i);
});

test("publishes absolute social metadata and keeps starter preview removed", async () => {
  const response = await render();
  const html = await response.text();
  const [page, layout, styles, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(html, /property="og:image" content="http:\/\/localhost:3000\/og\.png"/i);
  assert.match(html, /name="twitter:card" content="summary_large_image"/i);
  assert.match(page, /^"use client";/);
  assert.match(page, /localStorage\.getItem\("zchat-theme"\)/);
  assert.match(page, /localStorage\.setItem\("zchat-theme", theme\)/);
  assert.match(page, /Chọn phong cách hiển thị/);
  assert.match(page, /setActiveNav\("contacts"\)/);
  assert.match(page, /setInboxTab\("unread"\)/);
  assert.match(layout, /generateMetadata/);
  assert.match(layout, /Nunito/);
  assert.match(styles, /@keyframes icon-spring/);
  assert.match(styles, /@keyframes conversation-focus/);
  assert.match(styles, /font-synthesis:\s*none/);
  assert.match(styles, /\.design-studio\s*\{[^}]*color:\s*var\(--ink\)/s);
  assert.match(styles, /--bubble-in-ink:\s*#20344d/);
  assert.match(styles, /\.message-bubble\s*\{[^}]*color:\s*var\(--bubble-in-ink\)/s);
  assert.doesNotMatch(page, /_sites-preview|SkeletonPreview/);
  assert.doesNotMatch(layout, /codex-preview|_sites-preview/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);

  assert.deepEqual(
    await readdir(new URL("app/_sites-preview", projectRoot)),
    [],
  );
  await access(new URL("public/og.png", projectRoot));
});
