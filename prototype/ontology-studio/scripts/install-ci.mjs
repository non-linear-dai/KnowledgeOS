import { spawnSync } from "node:child_process";
import { accessSync, constants } from "node:fs";
import path from "node:path";
import { projectRoot } from "./sites-env.mjs";
import { readExecutionProfile } from "./execution-profile.mjs";

// The checked-in lockfile is pnpm-lock.yaml. Portable installs use the host's
// pnpm; managed Linux keeps its locked store and cache setup.
const managedLinux = readExecutionProfile() === "managed-linux";
if (managedLinux && process.platform !== "linux") {
  throw new Error("The managed-linux execution profile requires Linux.");
}
const result = managedLinux
  ? spawnSync("bash", [path.join(projectRoot, "scripts/install-pnpm.sh")], { stdio: "inherit" })
  : spawnSync(process.platform === "win32" ? "pnpm.cmd" : "pnpm",
      ["install", "--frozen-lockfile", "--prefer-offline"],
      { cwd: projectRoot, stdio: "inherit", shell: process.platform === "win32" });
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);

try {
  accessSync(path.join(projectRoot, "node_modules", ".bin",
    process.platform === "win32" ? "vinext.cmd" : "vinext"),
    process.platform === "win32" ? constants.F_OK : constants.X_OK);
} catch {
  console.error("pnpm exited successfully but the local vinext executable is unavailable.");
  process.exitCode = 69;
}
