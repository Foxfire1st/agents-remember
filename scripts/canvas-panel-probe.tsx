// Browser fixture: the real mounted revision frame. Its retained revision and target come from
// the fixture; allocation, delivery, messages and revocation use the product HTTP endpoints.
import { createRoot } from "../dashboard/node_modules/react-dom/client";
import { RevisionFrame } from "../dashboard/src/panels/review/explainer/RevisionFrame";

const fixture = await fetch("/proof/panel-state").then((response) =>
  response.json(),
);
const nativeFetch = window.fetch.bind(window);
let first = true;
window.fetch = async (input, init) => {
  const url = String(input);
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
  <RevisionFrame
    target={{
      repository: fixture.props.repository,
      master: fixture.props.master,
      leaf: fixture.props.leaf,
    }}
    revision={fixture.state.opening.revision}
    caption="Isolation proof"
  />,
);
