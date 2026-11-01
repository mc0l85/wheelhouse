// Dictation without the soft keyboard: tap 🎤 → Apple's recognizer (Safari's
// webkitSpeechRecognition) listens; tap again (or pause) → the transcript is
// typed into the pane that was focused when you tapped, then Enter goes to that
// SAME pane 10 s later. A tap during the countdown cancels the Enter and leaves
// the text in the input box for editing.
//
// Delivery is by pane ID (herdr send-text / send-keys), never through the
// terminal stream, so a focus change mid-countdown — from here, another
// client, or an agent spawning a pane — cannot misroute the Enter.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Pane } from "../api/types";
import { noBlur } from "./Keys";
import { paneName } from "./PaneSelector";

const SUBMIT_MS = 10_000;

interface Rec {
  lang: string; continuous: boolean; interimResults: boolean;
  start(): void; stop(): void; abort(): void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
}
type RecCtor = new () => Rec;
const RecImpl: RecCtor | undefined = typeof window !== "undefined"
  ? ((window as unknown as { SpeechRecognition?: RecCtor; webkitSpeechRecognition?: RecCtor }).SpeechRecognition
    ?? (window as unknown as { webkitSpeechRecognition?: RecCtor }).webkitSpeechRecognition)
  : undefined;

// Safari asks for the microphone on every recognizer start() unless the page
// already holds a getUserMedia grant, which lasts for the life of the page. Take
// one on the first tap (and drop the stream at once) so later taps don't prompt.
let micGranted = false;
async function primeMic() {
  if (micGranted || !navigator.mediaDevices?.getUserMedia) return;
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
    micGranted = true;
  } catch { /* denied: the recognizer reports it */ }
}

type Phase =
  | { k: "idle" }
  | { k: "listening"; target: Pane; text: string }
  | { k: "countdown"; target: Pane; text: string; until: number };

export function Mic({ flash }: { flash: (m: string, bad?: boolean) => void }) {
  const [ph, setPh] = useState<Phase>({ k: "idle" });
  const [now, setNow] = useState(Date.now());
  const rec = useRef<Rec | null>(null);
  const timer = useRef(0);
  const stopping = useRef(false);    // the user tapped stop (vs Safari ending on its own)
  const phRef = useRef(ph);
  phRef.current = ph;

  useEffect(() => () => { rec.current?.abort(); clearTimeout(timer.current); }, []);
  useEffect(() => {
    if (ph.k !== "countdown") return;
    const t = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(t);
  }, [ph.k]);

  async function deliver(target: Pane, text: string) {
    text = text.trim();
    if (!text) { setPh({ k: "idle" }); flash("heard nothing", true); return; }
    try { await api.sendText(target.pane_id, text); }
    catch (e) { setPh({ k: "idle" }); flash(`send failed: ${(e as Error).message}`, true); return; }
    const until = Date.now() + SUBMIT_MS;
    setNow(Date.now());
    setPh({ k: "countdown", target, text, until });
    timer.current = window.setTimeout(async () => {
      setPh({ k: "idle" });
      try { await api.sendKeys(target.pane_id, ["Enter"]); flash(`sent → ${paneName(target)} ✓`); }
      catch (e) { flash(`Enter failed: ${(e as Error).message}`, true); }
    }, SUBMIT_MS);
  }

  async function start() {
    if (!RecImpl) { flash("speech recognition not available in this browser", true); return; }
    let target: Pane | undefined;
    try { target = (await api.panes()).panes.find((p) => p.focused); } catch { /* below */ }
    if (!target) { flash("no focused pane", true); return; }
    await primeMic();
    // Safari's recognizer sometimes ends by itself before any words arrive
    // (silence at the start, an audio hiccup). Until the user taps stop, an
    // empty ending just starts a fresh recognizer; text already heard is kept
    // across restarts. Words are held in locals, not React state, so the
    // last interim result can't be lost to a render that hasn't happened yet.
    let committed = "";              // text from recognizers that already ended
    let current = "";                // final + interim text of the live one
    let restarts = 0;
    let lastError = "";
    const began = Date.now();
    stopping.current = false;
    const heard = () => (committed + " " + current).trim();
    const listen = () => {
      const r = new RecImpl!();
      r.lang = navigator.language || "en-US";
      r.continuous = true;
      r.interimResults = true;
      let finalText = "";
      r.onresult = (e) => {
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const res = e.results[i];
          if (res.isFinal) finalText += res[0].transcript; else interim += res[0].transcript;
        }
        current = finalText + interim;
        setPh((p) => p.k === "listening" ? { ...p, text: heard() } : p);
      };
      r.onerror = (e) => {
        lastError = e.error;
        if (e.error === "no-speech" || e.error === "aborted") return;
        flash(e.error === "not-allowed" ? "microphone permission denied" : `speech error: ${e.error}`, true);
      };
      r.onend = () => {
        rec.current = null;
        if (phRef.current.k !== "listening") return;
        committed = heard(); current = "";
        const fatal = lastError === "not-allowed" || lastError === "service-not-allowed";
        if (!stopping.current && !committed && !fatal && restarts < 20 && Date.now() - began < 60_000) {
          restarts++;
          lastError = "";
          listen();
          return;
        }
        if (!committed) {
          setPh({ k: "idle" });
          flash(`heard nothing${lastError ? ` (${lastError})` : ""}`, true);
          return;
        }
        void deliver(target!, committed);
      };
      rec.current = r;
      try { r.start(); } catch (e) { rec.current = null; setPh({ k: "idle" }); flash(`mic failed: ${(e as Error).message}`, true); }
    };
    setPh({ k: "listening", target, text: "" });
    listen();
  }

  function tap() {
    if (ph.k === "idle") void start();
    else if (ph.k === "listening") { stopping.current = true; rec.current?.stop(); }   // onend delivers
    else {
      clearTimeout(timer.current);
      setPh({ k: "idle" });
      flash("auto-submit cancelled — text left in the box");
    }
  }

  const left = ph.k === "countdown" ? Math.max(0, ph.until - now) : 0;
  const frac = ph.k === "countdown" ? left / SUBMIT_MS : 0;
  return (
    <>
      {ph.k !== "idle" && (
        // Over the top of the terminal (not the bar, too small to read; not the
        // bottom, where the agent's input box shows the delivered text).
        // pointer-events: none — taps fall through to the terminal.
        <div className={`mic-card ${ph.k}`}>
          <div className="mic-head">
            <span className="mic-state">{ph.k === "listening" ? "● listening · 🎤 stops" : `Enter in ${Math.ceil(left / 1000)}s · 🎤 cancels`}</span>
            <span className="mic-to">→ {paneName(ph.target)}</span>
          </div>
          <div className="mic-text"><span>{ph.text || "…"}</span></div>
          {ph.k === "countdown" && <div className="mic-bar" style={{ transform: `scaleX(${frac})` }} />}
        </div>
      )}
      <button {...noBlur} data-mic className={`trig mic-btn ${ph.k}`}
        title={ph.k === "idle" ? "dictate into the focused pane" : ph.k === "listening" ? "tap to stop" : "tap to cancel auto-submit"}
        style={ph.k === "countdown" ? { ["--p" as string]: frac } : undefined}
        onClick={tap}>
        <span className="mic-ico">🎤</span>
      </button>
    </>
  );
}
