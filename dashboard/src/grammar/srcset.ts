// The HTML source-set tokenizer keeps commas in URLs and parenthesized descriptors.
// Media admission belongs to the caller, after every candidate has been parsed.
// https://html.spec.whatwg.org/multipage/images.html#parse-a-srcset-attribute
export interface SrcSetCandidate {
  url: string;
  descriptor: string;
}

const asciiWhitespace = /[\t\n\f\r ]/;
const integerDescriptor = /^[0-9]+[wh]$/;
const densityDescriptor = /^-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?x$/;

function collectUntilSpace(value: string, position: number): number {
  while (position < value.length && !asciiWhitespace.test(value[position])) position += 1;
  return position;
}

function skipSeparators(value: string, position: number): number {
  while (position < value.length && (asciiWhitespace.test(value[position]) || value[position] === ",")) {
    position += 1;
  }
  return position;
}

function tokenizeDescriptors(value: string, position: number) {
  const descriptors: string[] = [];
  let current = "";
  let state: "descriptor" | "parentheses" | "after descriptor" = "descriptor";
  while (position < value.length) {
    const char = value[position];
    if (state === "parentheses") {
      current += char;
      if (char === ")") state = "descriptor";
    } else if (asciiWhitespace.test(char)) {
      if (current) descriptors.push(current);
      current = "";
      state = "after descriptor";
    } else if (state === "after descriptor") {
      state = "descriptor";
      continue;
    } else if (char === ",") {
      position += 1;
      break;
    } else {
      current += char;
      if (char === "(") state = "parentheses";
    }
    position += 1;
  }
  if (current) descriptors.push(current);
  return { descriptors, position };
}

interface DescriptorState {
  width: boolean;
  density: boolean;
  height: boolean;
}

function acceptIntegerDescriptor(descriptor: string, state: DescriptorState): boolean {
  if (!/[1-9]/.test(descriptor)) return false;
  const dimension = descriptor.endsWith("w") ? "width" : "height";
  if (state[dimension] || state.density) return false;
  state[dimension] = true;
  return true;
}

function acceptDensityDescriptor(descriptor: string, state: DescriptorState): boolean {
  const number = Number(descriptor.slice(0, -1));
  if (state.width || state.density || state.height || !Number.isFinite(number) || number < 0) return false;
  state.density = true;
  return true;
}

function acceptDescriptor(descriptor: string, state: DescriptorState): boolean {
  if (integerDescriptor.exec(descriptor)?.[0] === descriptor) return acceptIntegerDescriptor(descriptor, state);
  if (densityDescriptor.exec(descriptor)?.[0] === descriptor) return acceptDensityDescriptor(descriptor, state);
  return false;
}

function validDescriptors(descriptors: string[]): boolean {
  const state: DescriptorState = { width: false, density: false, height: false };
  for (const descriptor of descriptors) {
    if (!acceptDescriptor(descriptor, state)) return false;
  }
  return !state.height || state.width;
}

export function parseSrcSet(value: string): SrcSetCandidate[] {
  const candidates: SrcSetCandidate[] = [];
  let position = 0;
  while (position < value.length) {
    position = skipSeparators(value, position);
    if (position >= value.length) break;
    const start = position;
    position = collectUntilSpace(value, position);
    let url = value.slice(start, position);
    let descriptors: string[] = [];
    if (url.endsWith(",")) {
      url = url.replace(/,+$/, "");
    } else {
      const tokenized = tokenizeDescriptors(value, position);
      descriptors = tokenized.descriptors;
      position = tokenized.position;
    }
    if (validDescriptors(descriptors)) candidates.push({ url, descriptor: descriptors.join(" ") });
  }
  return candidates;
}
