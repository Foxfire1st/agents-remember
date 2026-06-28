import mc2 from "./reference/mc2.html?raw";

// mc2 (the canonical design endpoint, note 07) mounted read-only for side-by-side comparison
// with the real cockpit. Imported as `?raw` and rendered via srcDoc so it lives only in the
// DEV-only chunk and never ships in the production bundle.
export function Reference() {
  return (
    <div className="reference">
      <div className="reference__bar">mc2 — canonical design endpoint (note 07) · read-only</div>
      <iframe title="mc2 reference" srcDoc={mc2} className="reference__frame" />
    </div>
  );
}
