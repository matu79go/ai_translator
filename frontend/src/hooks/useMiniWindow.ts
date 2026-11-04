import { useCallback, useEffect, useState } from 'react';

/**
 * Always-on-top mini window via the Document Picture-in-Picture API (desktop Chrome / Edge 116+).
 * The React tree stays in the main page; only the rendered output is portaled into the PiP window,
 * so state, WebSocket and audio capture keep living in the main page.
 */

type DocumentPip = { requestWindow(options?: { width?: number; height?: number }): Promise<Window> };

function getDocumentPip(): DocumentPip | undefined {
  return (window as unknown as { documentPictureInPicture?: DocumentPip }).documentPictureInPicture;
}

export const isMiniWindowSupported = () => !!getDocumentPip();

// Copy the page's styles into the PiP document (Vite dev = <style> tags, build = <link> stylesheets)
function copyStyles(target: Document) {
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const style = target.createElement('style');
      style.textContent = Array.from(sheet.cssRules).map((r) => r.cssText).join('\n');
      target.head.appendChild(style);
    } catch {
      if (!sheet.href) continue;
      const link = target.createElement('link');
      link.rel = 'stylesheet';
      link.href = sheet.href;
      target.head.appendChild(link);
    }
  }
}

export function useMiniWindow(width = 520, height = 170) {
  const [win, setWin] = useState<Window | null>(null);

  const open = useCallback(async () => {
    const pip = getDocumentPip();
    if (!pip) return;
    const w = await pip.requestWindow({ width, height });  // must be called from a user gesture (click)
    copyStyles(w.document);
    w.document.title = 'AI Translator';
    w.document.body.style.margin = '0';
    w.addEventListener('pagehide', () => setWin(null), { once: true });
    setWin(w);
  }, [width, height]);

  const close = useCallback(() => win?.close(), [win]);

  // Close the mini window when the page unmounts
  useEffect(() => () => win?.close(), [win]);

  return { win, open, close };
}
