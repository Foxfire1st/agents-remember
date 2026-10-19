// Browser fixture: the real mounted production panel. Only its saved-state read is a fixture;
// allocation, frame delivery, messages and revocation use the actual product HTTP endpoints.
import { createRoot } from "../dashboard/node_modules/react-dom/client";
import { CanvasPanel } from "../dashboard/src/panels/review/canvas/CanvasPanel";

const fixture = await fetch("/proof/panel-state").then((response) =>
  response.json(),
);
const nativeFetch = window.fetch.bind(window);
let first = true;
window.fetch = async (input, init) => {
  const url = String(input);
  if (url.startsWith("/api/canvas/frames/"))
    return Response.json(fixture.state);
  const response = await nativeFetch(input, init);
  if (
    url === "/api/canvas/sessions" &&
    first &&
    location.search.includes("navigate=1")
  ) {
    first = false;
    const opened = await response.json();
    return Response.json({
      ...opened,
      pageUrl: `${opened.pageUrl}?mode=selfnav`,
    });
  }
  return response;
};
createRoot(document.getElementById("root")!).render(
  <CanvasPanel {...fixture.props} />,
);
