/* An authored proof fixture, not a presenter output or a prescribed page template. */
import { createHighlighter } from "./kit/shiki/4.5.0/shiki-kit.js";
import {
  scene,
  createKit,
  renderFrame,
  paint,
  Rect,
  Txt,
  waitFor,
} from "./kit/motion-canvas/3.17.2/motion-canvas-kit.js";
import {
  THEME_CSS,
  FORM_STYLES_CSS,
} from "./kit/open-generative-ui/0.1.0-8391a94/open-generative-ui-kit.js";
import {
  col,
  ease,
  Proj,
  orbit,
  strokeSegs,
  curveSegs,
  v3,
} from "./kit/ar-video/23fd71bf/ar-video-vocabulary.js";

const observations = {};
const highlighter = await createHighlighter();
document.querySelector("#code").innerHTML = highlighter.codeToHtml(
  "const answer = 42",
  { lang: "javascript", theme: "github-dark" },
);
const morph = document.createElement("shiki-magic-move");
morph.highlighter = highlighter;
morph.lang = "javascript";
morph.theme = "github-dark";
morph.code = "const answer = 41";
document.querySelector("#code").append(morph);
await new Promise((resolve) => setTimeout(resolve, 100));
morph.code = "const answer = 42";
await new Promise((resolve) => setTimeout(resolve, 100));
observations.shiki = {
  highlighted: document.querySelectorAll("#code span").length,
  morphDefined: !!customElements.get("shiki-magic-move"),
  code: morph.code,
};

const style = document.createElement("style");
style.textContent = THEME_CSS + "\n" + FORM_STYLES_CSS;
document.head.append(style);
observations.openGenerativeUi = {
  token: getComputedStyle(document.documentElement)
    .getPropertyValue("--color-text-primary")
    .trim(),
  button: document.querySelector("button").textContent,
};

const stage = document.querySelector("#vocabulary"),
  context = stage.getContext("2d");
const projection = new Proj(orbit(v3(0, 0, 0), 900, 0.4, 0.2));
strokeSegs(
  context,
  projection,
  curveSegs(v3(0, 0, 0), v3(200, 180, 0), v3(400, 0, 0), 24),
  (alpha) => col("amber", alpha * projection.fog(2000)),
);
context.fillStyle = col("cyan", ease.outExpo(0.5));
context.fillRect(24, 24, 120, 8);
observations.arVideo = {
  pixel: [...context.getImageData(30, 27, 1, 1).data],
  colour: context.fillStyle,
};

const kit = createKit({
  width: 480,
  height: 270,
  scenes: [
    scene("proof", function* (view) {
      view.add(
        new Rect({
          width: 300,
          height: 180,
          fill: "#245577",
          children: [
            new Txt({ text: "Motion Canvas", fill: "#ffffff", fontSize: 24 }),
          ],
        }),
      );
      yield* waitFor(1);
    }),
  ],
});
await kit.player.prepare();
await renderFrame(kit, 10);
await kit.stage.render(kit.player.playback.currentScene, kit.player.playback.previousScene);
paint(kit, document.querySelector("#motion"));
observations.motionCanvas = {
  pixel: [
    ...document
      .querySelector("#motion")
      .getContext("2d")
      .getImageData(240, 135, 1, 1).data,
  ],
  size: [kit.stage.finalBuffer.width, kit.stage.finalBuffer.height],
};
kit.player.deactivate();
window.__kitProof = observations;
