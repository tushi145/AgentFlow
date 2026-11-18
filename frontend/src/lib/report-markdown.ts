/* Helpers for rendering generated reports: citation linking and heading anchors. */

/** Citations as emitted by the backend renderer after filtering: [S1] or [S1, S2, S3]. */
export const CITATION_RE = /\[(S\d+(?:\s*,\s*S\d+)*)\]/g;

export function slugify(text: string): string {
  return text.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}\s-]/gu, "").trim().replace(/\s+/g, "-").slice(0, 80) || "section";
}

/** Assigns stable, unique ids in document order (same algorithm for the TOC and the headings). */
export function makeIdAllocator() {
  const seen = new Map<string, number>();
  return (text: string) => {
    const base = slugify(text);
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return n ? `${base}-${n + 1}` : base;
  };
}

export interface TocEntry { id: string; text: string; level: 2 | 3 }

/** Level-2/3 headings outside fenced code blocks, with the ids the renderer will give them. */
export function tableOfContents(markdown: string): TocEntry[] {
  const alloc = makeIdAllocator();
  const out: TocEntry[] = [];
  let fenced = false;
  for (const line of markdown.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    if (fenced) continue;
    const m = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!m) continue;
    const text = m[2].replace(/[*_`]/g, "").replace(CITATION_RE, "").trim();
    const id = alloc(text);
    if (m[1].length >= 2) out.push({ id, text, level: m[1].length as 2 | 3 });
  }
  return out;
}

/** Scroll to a source card (rendered by SourcesPanel with id `source-<ref>`) and flash it. */
export function scrollToSource(ref: string) {
  const el = document.getElementById(`source-${ref}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("ring-2", "ring-primary/60");
  setTimeout(() => el.classList.remove("ring-2", "ring-primary/60"), 1600);
}

export const isWebUrl =(u: string | undefined | null): u is string => !!u && /^https?:\/\//i.test(u);

/* ---- remark plugin: split citation text into links to `#source-S<n>` (mdast, no HTML) ---- */

interface MdNode { type: string; value?: string; url?: string; children?: MdNode[] }

function splitCitations(value: string): MdNode[] | null {
  CITATION_RE.lastIndex = 0;
  if (!CITATION_RE.test(value)) return null;
  CITATION_RE.lastIndex = 0;
  const out: MdNode[] = [];
  let last = 0;
  for (const m of value.matchAll(CITATION_RE)) {
    if (m.index! > last) out.push({ type: "text", value: value.slice(last, m.index) });
    const refs = m[1].split(/\s*,\s*/);
    out.push({ type: "text", value: "[" });
    refs.forEach((r, i) => {
      if (i) out.push({ type: "text", value: ", " });
      out.push({ type: "link", url: `#source-${r}`, children: [{ type: "text", value: r }] });
    });
    out.push({ type: "text", value: "]" });
    last = m.index! + m[0].length;
  }
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

function walk(node: MdNode) {
  if (!node.children || node.type === "link" || node.type === "linkReference") return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    const parts = child.type === "text" && child.value ? splitCitations(child.value) : null;
    if (parts) next.push(...parts);
    else {
      walk(child);
      next.push(child);
    }
  }
  node.children = next;
}

export function remarkCitations() {
  return (tree: MdNode) => walk(tree);
}

/* ---- remark plugin: heading ids assigned at parse time (pure per parse, so React StrictMode's
   double rendering cannot shift them), using the same algorithm as tableOfContents() ---- */

type HeadingNode = MdNode & { depth?: number; data?: { hProperties?: Record<string, unknown> } };

function plainText(node: MdNode): string {
  if (node.type === "text" || node.type === "inlineCode") return node.value ?? "";
  return (node.children ?? []).map(plainText).join("");
}

export function remarkHeadingIds() {
  return (tree: MdNode) => {
    const alloc = makeIdAllocator();
    const visit = (node: HeadingNode) => {
      if (node.type === "heading") {
        const text = plainText(node).replace(CITATION_RE, "").trim();
        node.data = { ...node.data, hProperties: { ...node.data?.hProperties, id: alloc(text) } };
        return;
      }
      node.children?.forEach((c) => visit(c as HeadingNode));
    };
    visit(tree as HeadingNode);
  };
}
