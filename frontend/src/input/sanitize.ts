// Pure paste sanitiser (H0 skeleton; implemented in lane C).
/** Max decoded bytes accepted from an OSC 52 clipboard write. */
export const OSC52_MAX_BYTES = 1 << 20;

/** Drop ESC, C0 except \t \n \r, DEL and C1 (U+0080–U+009F). */
export function sanitizePaste(_text: string): string {
  throw new Error("todo");
}
