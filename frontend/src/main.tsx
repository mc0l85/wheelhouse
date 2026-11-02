import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { registerServiceWorker } from "./push";
import { watchForUpdates } from "./update";
// Self-hosted fonts (no third-party font CDN).
import "@fontsource/outfit/300.css";
import "@fontsource/outfit/400.css";
import "@fontsource/outfit/500.css";
import "@fontsource/outfit/600.css";
import "@fontsource/outfit/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./theme/glass.css";
import "./theme/wheel.css";

// Suppress the browser's native right-click menu app-wide. The "tabs" are drawn
// by the herdr TUI inside xterm.js, so a right-click there would otherwise pop
// the browser context menu (IE/Edge styling) on top of herdr's own chrome.
window.addEventListener("contextmenu", (e) => e.preventDefault());

registerServiceWorker();
watchForUpdates();

// The canvas renderer measures the cell and caches glyphs the moment the
// terminal opens. If JetBrains Mono is still loading, it measures and draws the
// fallback font, and the text comes out mis-sized with a wavy baseline. So load
// the terminal font first (it is self-hosted; capped at 3 s so a failure only
// costs a fallback font).
const termFont = Promise.all(["400", "500"].map((w) => document.fonts.load(`${w} 13px "JetBrains Mono"`)));
Promise.race([termFont, new Promise((r) => setTimeout(r, 3000))])
  .catch(() => {})
  .then(() => {
    ReactDOM.createRoot(document.getElementById("root")!).render(
      <React.StrictMode>
        <App />
      </React.StrictMode>,
    );
  });
