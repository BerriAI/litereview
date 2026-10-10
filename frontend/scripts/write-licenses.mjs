// Preserve dependency notices when node_modules is replaced by the browser bundle.
import { copyFileSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const lock = JSON.parse(readFileSync(join(frontend, "package-lock.json"), "utf8"));
const sections = ["Third-party licenses for the LiteReview browser application.\n"];

for (const [path, info] of Object.entries(lock.packages).sort(([a], [b]) => a.localeCompare(b))) {
  if (!path || info.dev) continue;
  const directory = join(frontend, path);
  const metadata = JSON.parse(readFileSync(join(directory, "package.json"), "utf8"));
  if (metadata.version !== info.version) throw new Error(`Run npm ci: ${path} differs from the lockfile.`);
  const notices = readdirSync(directory).filter((name) =>
    /^(licen[cs]e|copying|notice)([.-]|$)/i.test(name) && statSync(join(directory, name)).isFile(),
  ).sort();
  if (!notices.length) throw new Error(`Missing license text for ${path}; review before distributing.`);
  sections.push(`\n${"=".repeat(72)}\n${metadata.name} ${info.version}\nLicense: ${info.license || metadata.license}\n`);
  for (const name of notices) {
    sections.push(`\n--- ${name} ---\n${readFileSync(join(directory, name), "utf8").trim()}\n`);
  }
}

writeFileSync(join(frontend, "dist", "third-party-licenses.txt"), sections.join(""));
copyFileSync(join(frontend, "..", "LICENSE"), join(frontend, "dist", "LICENSE.txt"));
copyFileSync(join(frontend, "..", "THIRD_PARTY_NOTICES.md"), join(frontend, "dist", "THIRD_PARTY_NOTICES.md"));
console.log("Included MIT license and production dependency notices in dist/.");
