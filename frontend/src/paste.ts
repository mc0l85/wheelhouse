// Image paste: the PTY only takes text, so a clipboard image is uploaded to the
// server and its file path is pasted instead. Claude Code turns a pasted image
// path into an attachment ("[Image #1]"); other agents can open the file.
import { api } from "./api/client";

// An image on the clipboard, unless real text came with it (a spreadsheet copy
// carries a PNG of the cells too; the text is what was meant). A lone URL or
// file name next to an image is just its label, so the image wins.
export function clipboardImage(dt: DataTransfer | null): Blob | null {
  if (!dt) return null;
  const item = Array.from(dt.items ?? []).find((i) => i.kind === "file" && i.type.startsWith("image/"));
  const img = item?.getAsFile() ?? Array.from(dt.files ?? []).find((f) => f.type.startsWith("image/")) ?? null;
  if (!img) return null;
  const text = dt.getData("text/plain").trim();
  return text && /\s/.test(text) ? null : img;
}

// Async Clipboard API read (the Controls › Paste button): image first, else text.
// `types` lists what was on the clipboard when neither was usable.
export async function readClipboard(): Promise<{ image?: Blob; text?: string; types?: string[] }> {
  if (navigator.clipboard?.read) {
    const items = await navigator.clipboard.read();
    for (const it of items) {
      const t = it.types.find((x) => x.startsWith("image/"));
      if (t) return { image: await it.getType(t) };
    }
    for (const it of items) {
      if (it.types.includes("text/plain")) return { text: await (await it.getType("text/plain")).text() };
    }
    return { types: items.flatMap((it) => it.types) };
  }
  return { text: await navigator.clipboard.readText() };
}

export async function pasteImage(img: Blob, paste: (text: string) => void,
                                 flash: (m: string, bad?: boolean) => void) {
  flash("uploading image…");
  try {
    const { path, bytes } = await api.uploadImage(img);
    paste(path);
    flash(`image pasted ✓ (${Math.max(1, Math.round(bytes / 1024))} KB)`);
  } catch (e) {
    flash(`image paste failed: ${(e as Error).message}`, true);
  }
}
