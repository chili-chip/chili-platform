// Refuse to deploy an environment from the wrong git branch.
// Usage: node scripts/check-branch.mjs <branch>
// Cloudflare and GitHub Actions builds may check out a detached HEAD, so
// their branch env vars win.
import { execSync } from "node:child_process";

const expected = process.argv[2];
const branch =
  process.env.WORKERS_CI_BRANCH ||
  process.env.CF_PAGES_BRANCH ||
  (process.env.GITHUB_EVENT_NAME === "push" && process.env.GITHUB_REF_NAME) ||
  execSync("git rev-parse --abbrev-ref HEAD").toString().trim();

if (branch !== expected) {
  console.error(`This environment deploys from the "${expected}" branch, but you are on "${branch}".`);
  process.exit(1);
}
