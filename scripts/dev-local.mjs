/**
 * Start the local Worker without the Workers AI binding.
 *
 * That binding always opens a remote session and waits for `wrangler login`,
 * so `pywrangler dev` never listens. The chat then has no API to answer.
 * `npm run dev:ai` keeps the binding for a live model call.
 */
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(join(root, "wrangler.jsonc"), "utf8");
// Line comments only. Glob strings such as "**/*.pyc" contain "/*", so a
// block-comment strip would delete the python_modules exclude list.
const json = source.replace(/^\s*\/\/.*$/gm, "").replace(/,(\s*[}\]])/g, "$1");
const config = JSON.parse(json);
delete config.ai;
if (config.ai) {
  throw new Error("local dev config still has an ai binding");
}
const configPath = join(root, ".wrangler-dev.jsonc");
writeFileSync(configPath, JSON.stringify(config, null, 2));

const child = spawn(
  "uv",
  ["run", "pywrangler", "dev", "-c", ".wrangler-dev.jsonc", ...process.argv.slice(2)],
  { cwd: root, stdio: "inherit", env: process.env },
);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(code ?? 1);
  }
});
