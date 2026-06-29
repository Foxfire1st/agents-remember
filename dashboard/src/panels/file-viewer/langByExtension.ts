// Map the L1 `language` id (from /api/files/read) to a CodeMirror 6 language extension,
// lazily imported so every @codemirror/lang-* pack is code-split (loaded only when a file
// of that language is first opened). Languages without an official pack — bash/toml/yaml/
// sql/text/binary — render as plain text (return null); pull @codemirror/legacy-modes later
// only if a real need appears. `javascript()` covers js/ts/jsx/tsx via its options.
import type { Extension } from "@codemirror/state";

export async function langExtension(language: string): Promise<Extension | null> {
  switch (language) {
    case "typescript": {
      const { javascript } = await import("@codemirror/lang-javascript");
      return javascript({ typescript: true });
    }
    case "tsx": {
      const { javascript } = await import("@codemirror/lang-javascript");
      return javascript({ typescript: true, jsx: true });
    }
    case "javascript": {
      const { javascript } = await import("@codemirror/lang-javascript");
      return javascript();
    }
    case "jsx": {
      const { javascript } = await import("@codemirror/lang-javascript");
      return javascript({ jsx: true });
    }
    case "python": {
      const { python } = await import("@codemirror/lang-python");
      return python();
    }
    case "json": {
      const { json } = await import("@codemirror/lang-json");
      return json();
    }
    case "css": {
      const { css } = await import("@codemirror/lang-css");
      return css();
    }
    case "html": {
      const { html } = await import("@codemirror/lang-html");
      return html();
    }
    case "markdown": {
      const { markdown } = await import("@codemirror/lang-markdown");
      return markdown();
    }
    default:
      return null; // bash / toml / yaml / sql / text / binary -> plain text
  }
}
