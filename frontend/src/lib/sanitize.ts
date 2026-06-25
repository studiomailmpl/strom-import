/**
 * HTML sanitizer — uses the browser's DOMParser to parse HTML into a real DOM tree,
 * then walks it and keeps only whitelisted tags and attributes.
 * Falls back to stripping all tags on the server side (SSR).
 */

const ALLOWED_TAGS = new Set([
  "p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li",
  "h1", "h2", "h3", "h4", "h5", "h6", "span", "div",
]);

// No attributes are allowed — this prevents all attribute-based XSS vectors.
// If href support is needed in the future, add explicit URL validation.

function sanitizeNode(node: Node, doc: Document): Node | null {
  if (node.nodeType === 3 /* TEXT_NODE */) {
    return doc.createTextNode(node.textContent || "");
  }

  if (node.nodeType !== 1 /* ELEMENT_NODE */) {
    return null;
  }

  const el = node as Element;
  const tagName = el.tagName.toLowerCase();

  if (!ALLOWED_TAGS.has(tagName)) {
    // For disallowed tags, keep their text children but drop the tag
    const fragment = doc.createDocumentFragment();
    for (let i = 0; i < el.childNodes.length; i++) {
      const child = sanitizeNode(el.childNodes[i], doc);
      if (child) fragment.appendChild(child);
    }
    return fragment;
  }

  // Create a clean element with no attributes
  const clean = doc.createElement(tagName);

  // Recursively sanitize children
  for (let i = 0; i < el.childNodes.length; i++) {
    const child = sanitizeNode(el.childNodes[i], doc);
    if (child) clean.appendChild(child);
  }

  return clean;
}

export function sanitizeHtml(html: string): string {
  // Server-side fallback: strip all HTML tags
  if (typeof window === "undefined" || typeof DOMParser === "undefined") {
    return html.replace(/<[^>]*>/g, "");
  }

  const parser = new DOMParser();
  const parsed = parser.parseFromString(html, "text/html");
  const body = parsed.body;

  const doc = document.implementation.createHTMLDocument("");
  const fragment = doc.createDocumentFragment();

  for (let i = 0; i < body.childNodes.length; i++) {
    const child = sanitizeNode(body.childNodes[i], doc);
    if (child) fragment.appendChild(child);
  }

  const container = doc.createElement("div");
  container.appendChild(fragment);
  return container.innerHTML;
}
