// Pure paste sanitiser.
/** Max decoded bytes accepted from an OSC 52 clipboard write. */
export const OSC52_MAX_BYTES = 1 << 20;

// ESC, C0 except \t \n \r, DEL and C1 (U+0080-U+009F).
// eslint-disable-next-line no-control-regex
const UNSAFE = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/g;

/** Drop ESC, C0 except \t \n \r, DEL and C1 (U+0080–U+009F). */
export function sanitizePaste(text: string): string {
  return text.replace(UNSAFE, "");
}
