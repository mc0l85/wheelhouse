import { execSync } from "node:child_process";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The commit this bundle is built from, for the update check (Controls › Updates).
// VITE_BUILD_SHA wins (a build from an exported tree has no .git); else git; else "" = no check.
function buildSha() {
  const env = (process.env.VITE_BUILD_SHA || "").trim().toLowerCase();
  if (/^[0-9a-f]{7,40}$/.test(env)) return env;
  try { return execSync("git rev-parse HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim(); }
  catch { return ""; }
}

export default defineConfig({
  plugins: [react()], base: "/", build: { sourcemap: false },
  define: { __BUILD_SHA__: JSON.stringify(buildSha()) },
});
