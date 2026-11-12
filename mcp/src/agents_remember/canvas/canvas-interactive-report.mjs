/** D565: full-motion observations accompany render; they never decide publication.
 *
 * A run observes every pass to its end. It has no clock of its own guessing at the work: it stops
 * only when the load process's own time limit (the caller's --time-limit) is too near to finish one
 * more control and still answer -- measured on L183 and L191, all five passes take 74-120 s, well
 * inside it. A run that does stop is never a silent cut (MIK-R211 v5): every pass and control it
 * did not reach is named in the report, and the next render of the same draft resumes it -- passes
 * already complete are carried, controls already visited keep their status and pictures, and only
 * the rest are visited.
 */
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

/** One action's own bound (click, key, picture) and a page load's: an action that takes longer is
 * that control's error, never a wait. */
const ACTION_MS = 1500;
const LOAD_MS = 10000;
/** What a run keeps free before the load's time limit: a pass's page load, one control and its
 * picture, and the answer, so a stop always ends in a written report. */
export const RESERVE_MS = LOAD_MS + 2 * ACTION_MS + 2000;
const MODES = [
  { width: 1280, method: "click", effects: true },
  { width: 1280, method: "keyboard", effects: true },
  { width: 390, method: "click", effects: true },
  { width: 390, method: "keyboard", effects: true },
  { width: 1280, method: "keyboard", effects: false },
];

function passName({ width, method, effects }) {
  return `${width}-${effects ? method : `effects-off-${method}`}`;
}

/** Whose the limit is, in the words every report and stop carries: the browser load process's own
 * window (canvas/prospective.py), not a limit on pages and not a product cap. */
const windowOf = (limitSeconds) =>
  `the browser load process's own ${limitSeconds} s window (canvas/prospective.py; not a limit on pages or a product cap)`;
const notReached = (limitSeconds) =>
  `${windowOf(limitSeconds)} was too near to go on; render the same draft again to visit only what is left`;

const CONTROLS = [
  "button",
  "input:not([type=hidden])",
  "select",
  "textarea",
  "summary",
  "a[href]",
  '[role="button"]',
  '[role="tab"]',
  '[role="switch"]',
  '[role="checkbox"]',
  '[role="radio"]',
  '[role="slider"]',
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function keyboardKey(control) {
  if (
    control.tag === "SELECT" ||
    control.type === "range" ||
    control.role === "slider"
  )
    return "ArrowRight";
  if (
    ["checkbox", "radio"].includes(control.type) ||
    ["switch", "checkbox", "radio"].includes(control.role)
  )
    return "Space";
  return "Enter";
}

async function observePass(browser, url, directory, mode, run) {
  const { width, method, effects } = mode;
  const { deadline, limitSeconds, comparison, earlier } = run;
  const report = {
    width,
    height: 720,
    method,
    reducedMotion: "no-preference",
    effects,
    errors: [],
    controls: [],
    pictures: [],
    complete: false,
  };
  const context = await browser.newContext({
    viewport: { width, height: 720 },
    reducedMotion: "no-preference",
  });
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(ACTION_MS);
    page.on("console", (message) => {
      if (message.type() === "error")
        report.errors.push(`console: ${message.text()}`);
    });
    page.on("pageerror", (error) =>
      report.errors.push(`pageerror: ${error.message}`),
    );
    page.on("requestfailed", (request) =>
      report.errors.push(
        `request: ${request.url()} ${request.failure()?.errorText ?? "failed"}`,
      ),
    );
    await page.goto(url, { waitUntil: "load", timeout: LOAD_MS });
    await page.evaluate(
      (binding) =>
        dispatchEvent(
          new MessageEvent("message", {
            data: binding,
            source: parent,
          }),
        ),
      {
        schema: "ar-canvas-binding/v1",
        protocolVersion: 1,
        session: "prospective-diagnostic",
        revision: 0,
        comparison,
        effects,
      },
    );
    const controls = await page.locator(CONTROLS).elementHandles();
    // A resumed pass keeps what the earlier run saw, but only for the same inventory: a different
    // count of controls means a different page, and then the pass is observed afresh.
    const kept =
      earlier && earlier.controls.length === controls.length ? earlier : null;
    if (earlier && !kept)
      report.errors.push(
        `resume: the earlier run saw ${earlier.controls.length} controls and this one ${controls.length}; the pass is observed afresh`,
      );
    if (kept) {
      report.resumedFrom = kept.run;
      report.earlierPictures = kept.pictures.map(
        (name) => `${kept.run}/${name}`,
      );
    }
    for (const [index, handle] of controls.entries()) {
      const description = await handle.evaluate((element) => ({
        tag: element.tagName,
        type: element.getAttribute("type") || "",
        role: element.getAttribute("role") || "",
        name:
          element.getAttribute("aria-label") ||
          element.textContent.trim() ||
          element.id ||
          element.getAttribute("name") ||
          "",
      }));
      const before = kept?.controls[index];
      report.controls.push(
        before && before.status !== "unvisited"
          ? { ...before, index, ...description, run: before.run ?? kept.run }
          : { index, ...description, status: "unvisited" },
      );
    }
    const picture = async (suffix) => {
      const name = `${passName(mode)}-${suffix}.png`;
      await page.screenshot({
        path: join(directory, name),
        timeout: ACTION_MS,
      });
      report.pictures.push(name);
    };
    if (!kept) await picture("initial");
    for (const [index, handle] of controls.entries()) {
      const control = report.controls[index];
      if (control.status !== "unvisited") continue;
      if (Date.now() >= deadline) {
        const left = report.controls.filter(
          (one) => one.status === "unvisited",
        ).length;
        report.errors.push(
          `${left} control(s) not visited: ${notReached(limitSeconds)}`,
        );
        break;
      }
      try {
        if (!(await handle.isVisible())) {
          control.status = "hidden";
          continue;
        }
        if (!(await handle.isEnabled())) {
          control.status = "disabled";
          continue;
        }
        if (method === "click") await handle.click({ timeout: ACTION_MS });
        else {
          await handle.focus();
          control.key = keyboardKey(control);
          await handle.press(control.key, { timeout: ACTION_MS });
        }
        control.status = "exercised";
        // Observe the event's next rendering opportunity without disabling motion.
        await page.waitForTimeout(100);
        await picture(`control-${index}`);
      } catch (error) {
        control.status = "error";
        control.error = error.message;
      }
    }
    report.motion = await page.evaluate(() => ({
      animations: document
        .getAnimations()
        .filter((animation) => animation.playState === "running").length,
      playing: [...document.querySelectorAll("audio,video")].filter(
        (media) => !media.paused && !media.ended,
      ).length,
    }));
    report.complete = !report.controls.some(
      (control) => control.status === "unvisited",
    );
  } catch (error) {
    report.errors.push(error.message);
  } finally {
    await context.close();
  }
  return report;
}

/** The earlier run's pass for ``mode``, tagged with the run folder its pictures lie in.
 *  A pass the earlier run never started saw no controls, so there is nothing of it to resume. */
function earlierPass(resume, mode) {
  const found = (resume?.passes ?? []).find(
    (pass) =>
      pass.width === mode.width &&
      pass.method === mode.method &&
      pass.effects === mode.effects,
  );
  return found?.controls?.length
    ? { ...found, run: found.run ?? resume.run }
    : null;
}

/** Observe every pass, resuming ``resume`` (the earlier run's report of the same draft) if given.
 * ``limit`` is the load process's own time limit: when it started and how many seconds it has. */
export async function interactiveReport(
  browser,
  url,
  directory,
  comparison,
  resume = null,
  { started = Date.now(), limitSeconds = 0 } = {},
) {
  const report = {
    reportOnly: true,
    state: "observed",
    passes: [],
    errors: [],
    unvisited: [],
    coverage:
      "Initially present controls, once per input method and viewport. Hidden/disabled controls are named but not activated; dynamically added controls are not inventoried.",
  };
  if (resume) report.resumedFrom = resume.run;
  // The one bound on a run, named with whose it is.
  report.window =
    limitSeconds > 0
      ? windowOf(limitSeconds)
      : "none: no time limit was given, so the run observes every pass to its end";
  // No time limit given: the run observes to the end. Given one: it stops resumably before it.
  const deadline =
    limitSeconds > 0 ? started + limitSeconds * 1000 - RESERVE_MS : Infinity;
  try {
    await mkdir(directory, { recursive: true });
    for (const mode of MODES) {
      const earlier = earlierPass(resume, mode);
      if (earlier?.complete) {
        report.passes.push({ ...earlier, carried: true });
        continue;
      }
      if (Date.now() >= deadline) {
        report.passes.push({
          ...mode,
          complete: false,
          errors: [`pass not visited: ${notReached(limitSeconds)}`],
          controls: earlier?.controls ?? [],
          pictures: [],
          ...(earlier ? { resumedFrom: earlier.run } : {}),
        });
      } else {
        report.passes.push(
          await observePass(browser, url, directory, mode, {
            deadline,
            limitSeconds,
            comparison,
            earlier,
          }),
        );
      }
    }
  } catch (error) {
    report.state = "unavailable";
    report.errors.push(error.message);
  }
  // Every pass this run left incomplete, by name, with the controls it did not reach.
  for (const pass of report.passes) {
    if (pass.complete) continue;
    report.unvisited.push({
      pass: passName(pass),
      controls: pass.controls.filter((one) => one.status === "unvisited")
        .length,
      started: pass.controls.length > 0,
    });
  }
  return report;
}
