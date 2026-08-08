// Minimal DOM helpers so the UI stays framework-free (no React, per spec).

export function el(tag: string, attrs: Record<string, any> = {}, children: (Node | string)[] = []): HTMLElement {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.setAttribute("style", v);
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v !== undefined && v !== null && v !== false) {
      node.setAttribute(k, String(v));
    }
  }
  for (const c of children) node.append(typeof c === "string" ? document.createTextNode(c) : c);
  return node;
}

export function clear(node: HTMLElement) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

let activeModal: HTMLElement | null = null;

export function openModal(title: string, body: HTMLElement, wide = false): HTMLElement {
  closeModal();
  const closeBtn = el("button", { class: "modal-close", onclick: () => closeModal() }, ["×"]);
  const panel = el("div", { class: "modal-panel" + (wide ? " wide" : "") }, [
    el("div", { class: "modal-head" }, [el("h3", {}, [title]), closeBtn]),
    body,
  ]);
  const overlay = el("div", { class: "modal-overlay", onclick: (e: any) => { if (e.target === overlay) closeModal(); } }, [panel]);
  document.body.append(overlay);
  activeModal = overlay;
  return overlay;
}

export function closeModal() {
  if (activeModal) { activeModal.remove(); activeModal = null; }
}
