import { describe, expect, it } from "vitest";

import { parseSrcSet } from "./srcset";

type Candidate = [url: string, descriptor: string];

const parsingCases: { name: string; input: string; expected: Candidate[] }[] = [
  { name: "empty input", input: "", expected: [] },
  { name: "only separators", input: "\t,\n,, \f\r", expected: [] },
  { name: "no descriptor at EOF", input: "logo.png", expected: [["logo.png", ""]] },
  { name: "no descriptor before whitespace and comma", input: "logo.png , local.png", expected: [["logo.png", ""], ["local.png", ""]] },
  { name: "a trailing URL comma ends the candidate", input: "logo.png, local.png 2x", expected: [["logo.png", ""], ["local.png", "2x"]] },
  { name: "multiple trailing URL commas are removed", input: "logo.png,,, local.png 2x", expected: [["logo.png", ""], ["local.png", "2x"]] },
  { name: "ASCII whitespace around candidates", input: "\t,\nlogo.png,\f\r local.png 2x", expected: [["logo.png", ""], ["local.png", "2x"]] },
  { name: "descriptors at EOF", input: "logo.png 1x", expected: [["logo.png", "1x"]] },
  { name: "after-descriptor state at EOF", input: "logo.png 1x \t", expected: [["logo.png", "1x"]] },
  { name: "comma after descriptor whitespace", input: "logo.png 1x \t, local.png 2x", expected: [["logo.png", "1x"], ["local.png", "2x"]] },
  { name: "descriptor candidates need no whitespace after commas", input: "one.png 1x,two.png 2x,three.png 3x", expected: [["one.png", "1x"], ["two.png", "2x"], ["three.png", "3x"]] },
  { name: "commas within a URL are not separators", input: "one.png,two.png 2x", expected: [["one.png,two.png", "2x"]] },
  { name: "a comma-bearing URL can end with a separator", input: "one.png,two.png, three.png", expected: [["one.png,two.png", ""], ["three.png", ""]] },
  { name: "data URL with commas stays one URL", input: "data:image/png,https://forbidden.example/x.png 1x, logo.png 2x", expected: [["data:image/png,https://forbidden.example/x.png", "1x"], ["logo.png", "2x"]] },
  { name: "data URL without descriptor", input: "data:image/png;base64,YQ==, logo.png 2x", expected: [["data:image/png;base64,YQ==", ""], ["logo.png", "2x"]] },
  { name: "non-ASCII whitespace stays in the URL", input: "logo\u00a0wide.png 1x", expected: [["logo\u00a0wide.png", "1x"]] },
  { name: "vertical tab stays in the URL", input: "logo\u000bwide.png 1x", expected: [["logo\u000bwide.png", "1x"]] },
  { name: "parenthesized commas do not start a new candidate", input: "bad.png (one,two), local.png 2x", expected: [["local.png", "2x"]] },
  { name: "parenthesized whitespace stays in one descriptor", input: "bad.png (one, two) 1x, local.png 2x", expected: [["local.png", "2x"]] },
  { name: "EOF in parentheses rejects that candidate", input: "bad.png (one,two", expected: [] },
  { name: "unclosed parentheses consume later commas", input: "bad.png (one,two, local.png 2x", expected: [] },
  { name: "an invalid candidate leaves its neighbors", input: "one.png 1x,bad.png invalid,three.png 3x", expected: [["one.png", "1x"], ["three.png", "3x"]] },
];

const acceptedDescriptors = [
  "1x", "0x", "-0x", ".5x", "1.25x", "1e2x", "1e+2x", "1E-2x", "-1e-999x",
  "1w", "001w", "300w 200h", "200h 300w",
];

const rejectedDescriptors = [
  "0w", "000w", "-1w", "+1w", "1.5w", "1W", "1w 2w", "1x 2x", "1w 2x", "1x 2w",
  "1h", "0h 1w", "1w 0h", "1h 2h 3w", "1h 2x", "1x 2h", "1w 2h 3x",
  "-1x", "+1x", "1.x", "1e+x", "NaNx", "Infinityx", "1e999x", "1X", "invalid",
  "(1x)", "1x(unknown)", "1x\u00a0", "1x\u000b", "1w\u2028 2w", "1w\u2029 2w",
];

describe("HTML srcset parsing", () => {
  it.each(parsingCases)("$name", ({ input, expected }) => {
    expect(parseSrcSet(input)).toEqual(expected.map(([url, descriptor]) => ({ url, descriptor })));
  });

  it.each(acceptedDescriptors)("accepts the descriptor %s", (descriptor) => {
    expect(parseSrcSet(`logo.png ${descriptor}`)).toEqual([{ url: "logo.png", descriptor }]);
  });

  it.each(rejectedDescriptors)("rejects the descriptor %s without losing the next candidate", (descriptor) => {
    expect(parseSrcSet(`bad.png ${descriptor}, local.png 2x`)).toEqual([
      { url: "local.png", descriptor: "2x" },
    ]);
  });
});
