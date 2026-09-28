import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

const workersCI = process.env.WORKERS_CI === "1";
const branch = process.env.WORKERS_CI_BRANCH || "";
const hasSite = existsSync("terem-site/index.html");

if (!workersCI || branch !== "main" || !hasSite) {
  console.log("[TEREM] secondary Cloudflare deploy skipped", { workersCI, branch, hasSite });
  process.exit(0);
}

console.log("[TEREM] deploying dedicated Worker terem-visual-dna from existing Workers Builds auth");
const result = spawnSync(
  "npx",
  ["wrangler", "deploy", "--config", "wrangler.terem.jsonc"],
  { stdio: "inherit", shell: process.platform === "win32" }
);

if (result.status !== 0) {
  console.warn("[TEREM] secondary deploy failed; primary 4K deployment will continue");
  process.exit(0);
}

console.log("[TEREM] secondary deploy completed");
