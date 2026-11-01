import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { registerServiceWorker } from "./push";
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

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
