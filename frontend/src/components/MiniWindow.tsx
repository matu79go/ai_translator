import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** Renders children into the mini (Document Picture-in-Picture) window opened by useMiniWindow. */
export function MiniWindow({ win, children }: { win: Window | null; children: ReactNode }) {
  return win ? createPortal(children, win.document.body) : null;
}
