import { copyFile, mkdir } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);
const staticRoot = "control_center/static";
await mkdir(`${staticRoot}/css`, { recursive: true });
await mkdir(`${staticRoot}/vendor`, { recursive: true });

await run(process.execPath, [
  "node_modules/tailwindcss/lib/cli.js",
  "-i", "control_center/assets/input.css",
  "-o", `${staticRoot}/css/app.css`,
  "--minify"
]);
await copyFile("node_modules/htmx.org/dist/htmx.min.js", `${staticRoot}/vendor/htmx.min.js`);
await copyFile("node_modules/@alpinejs/csp/dist/cdn.min.js", `${staticRoot}/vendor/alpine-csp.min.js`);
await copyFile("node_modules/swagger-ui-dist/swagger-ui.css", `${staticRoot}/vendor/swagger-ui.css`);
await copyFile("node_modules/swagger-ui-dist/swagger-ui-bundle.js", `${staticRoot}/vendor/swagger-ui-bundle.js`);
