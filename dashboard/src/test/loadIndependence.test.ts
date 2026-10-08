import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from 'vitest';
import {
  createSourceFile,
  forEachChild,
  ScriptKind,
  ScriptTarget,
  SyntaxKind,
  type Node,
} from 'typescript';

type Finding = { line: number; pattern: string; rule: number };

function scan(source: string): Finding[] {
  const markers = new Set<number>();
  // The existing TypeScript parser identifies literal data, including regexes and template parts,
  // before comment removal; delimiter text inside a string cannot erase executable code.
  const parsed = createSourceFile('scan.tsx', source, ScriptTarget.Latest, true, ScriptKind.TSX);
  const characters = source.split('');
  const maskData = (node: Node) => {
    if (
      [
        SyntaxKind.StringLiteral,
        SyntaxKind.NoSubstitutionTemplateLiteral,
        SyntaxKind.RegularExpressionLiteral,
        SyntaxKind.TemplateHead,
        SyntaxKind.TemplateMiddle,
        SyntaxKind.TemplateTail,
        SyntaxKind.JsxText,
      ].includes(node.kind)
    ) {
      const start = node.getStart(parsed);
      const token = source.slice(start, node.end);
      const key = token.slice(1, -1);
      const quotedKey =
        node.kind === SyntaxKind.StringLiteral &&
        /^\s*:/.test(source.slice(node.end)) &&
        /^(?:timeout|testTimeout|hookTimeout)$/.test(key);
      for (let at = start; at < node.end; at += 1)
        if (characters[at] !== '\n' && !(quotedKey && at > start && at < node.end - 1))
          characters[at] = ' ';
      return;
    }
    forEachChild(node, maskData);
  };
  maskData(parsed);
  const masked = characters.join('').replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, (comment, offset) => {
    if (/^\/\/\s*load-independent:\s*\S/.test(comment))
      markers.add(source.slice(0, offset).split('\n').length - 1);
    return comment.replace(/[^\n]/g, ' ');
  });
  const code = masked.split('\n');
  const joined = masked;
  const clockNames = new Set<string>();
  for (const match of joined.matchAll(
    /\b(\w+)\s*=(?!=|>)[^;]*?(?:Date\.now|performance\.now)\s*\(/g,
  ))
    clockNames.add(match[1]);
  const findings: Finding[] = [];
  const report = (index: number, pattern: string, rule: number) => {
    if (markers.has(index) || markers.has(index - 1)) return;
    findings.push({ line: index + 1, pattern, rule });
  };
  const callEnd = (open: number) => {
    let depth = 0;
    for (let at = open; at < joined.length; at += 1) {
      if (joined[at] === '(') depth += 1;
      if (joined[at] === ')' && --depth === 0) return at;
    }
    return joined.length;
  };
  code.forEach((line, index) => {
    if (/\btimeout\s*:/.test(line)) report(index, 'timeout:', 8);
  });
  for (const match of joined.matchAll(
    /\b(vi\.setConfig|setTimeout|expect|(?:it|test)(?:\.(?:only|skip|concurrent|sequential|each))*|beforeEach|afterEach|beforeAll|afterAll)\s*\(/g,
  )) {
    const start = match.index;
    let end = callEnd(start + match[0].lastIndexOf('('));
    if (match[1].endsWith('.each')) {
      const invocation = /^\s*\(/.exec(joined.slice(end + 1));
      if (invocation) end = callEnd(end + invocation[0].length);
    }
    const call = joined.slice(start, end + 1);
    const index = joined.slice(0, start).split('\n').length - 1;
    if (match[1] === 'vi.setConfig' && /\b(?:testTimeout|hookTimeout)\s*:/.test(call))
      report(index, 'vi.setConfig timeout', 8);
    if (match[1] === 'setTimeout') {
      const delay = /,\s*([^,()]+)\s*\)$/.exec(call)?.[1].trim();
      if (delay !== undefined && delay !== '0') report(index, 'setTimeout nonzero delay', 12);
    }
    if (
      /^(?:(?:it|test)(?:\.(?:only|skip|concurrent|sequential|each))*|beforeEach|afterEach|beforeAll|afterAll)$/.test(
        match[1],
      ) &&
      /,\s*(?:\d[\d_]*(?:\.[\d_]*)?|\.[\d_]+)(?:[eE][+-]?[\d_]+)?\s*\)$/.test(call)
    ) {
      report(joined.slice(0, end).split('\n').length - 1, 'test or hook trailing bound', 8);
    }
    if (match[1] === 'expect') {
      const matcher = joined.slice(end + 1).split(';')[0];
      const clock =
        /(?:Date\.now|performance\.now)\s*\(/.test(call) ||
        [...clockNames].some((name) => new RegExp(`\\b${name}\\b`).test(call));
      if (clock && /^\s*\.(?:not\.)?toBe(?:Less|Greater)Than(?:OrEqual)?\s*\(/.test(matcher))
        report(index, 'elapsed wall-clock assertion', 11);
    }
  }

  return findings;
}

const patterns = [
  ['await waitFor(check, { timeout: 10 });', 8],
  ['await waitFor(check, { "timeout": 10 });', 8],
  ['vi.setConfig({ testTimeout: 10 });', 8],
  ['vi.setConfig({ hookTimeout: 10 });', 8],
  ['test("case", () => {\n}, 10);', 8],
  ['afterEach(() => {\n}, 10);', 8],
  ['test("case", run, 10);', 8],
  ['beforeAll(run, 10);', 8],
  ['test.each([1])("case", run, 10);', 8],
  ['it.only("case", run, 10);', 8],
  ['setTimeout(() => {\n  work();\n}, 10);', 12],
  ['vi.setConfig({\n  hookTimeout: 10\n});', 8],
  ['await new Promise(resolve => setTimeout(resolve, 10));', 12],
  ['expect(Date.now() - start).toBeLessThan(10);', 11],
  ['expect(performance.now() - start).toBeGreaterThan(10);', 11],
  ['const elapsed = Date.now() - start;\nexpect(elapsed).toBeLessThan(10);', 11],
  [
    'let elapsed;\nelapsed = performance.now() - start;\nexpect(elapsed).not.toBeGreaterThan(10);',
    11,
  ],
] as const;

test.each(patterns)('reports %s and accepts a justified marker', (source, rule) => {
  const findings = scan(source);
  expect(findings).toHaveLength(1);
  expect(findings[0].rule).toBe(rule);
  const lines = source.split('\n');
  lines[findings[0].line - 1] += ' // load-independent: delay on the fake clock';
  expect(scan(lines.join('\n'))).toEqual([]);
  lines[findings[0].line - 1] = source.split('\n')[findings[0].line - 1];
  lines.splice(findings[0].line - 1, 0, '// load-independent: delay on the fake clock');
  expect(scan(lines.join('\n'))).toEqual([]);
});

test('accepts event-loop turns and ordinary clock data', () => {
  expect(scan('setTimeout(resolve, 0);\nexpect(Date.now()).toBe(123);\n/* timeout: 10 */')).toEqual(
    [],
  );
});

test('marker text inside a string or property key is not a comment', () => {
  for (const source of [
    'const reason = "// load-independent: ignored"; waitFor(check, { timeout: 10 });',
    'waitFor(check, { "// load-independent: ignored": 1, timeout: 10 });',
  ]) {
    expect(scan(source)).toEqual([{ line: 1, pattern: 'timeout:', rule: 8 }]);
  }
});

test.each([
  [
    'const elapsed =\n Date.now()-start;\nexpect(elapsed).toBeLessThan(10);',
    { line: 3, pattern: 'elapsed wall-clock assertion', rule: 11 },
  ],
  ['test("case", run, 1e3);', { line: 1, pattern: 'test or hook trailing bound', rule: 8 }],
  [
    'const text="/*";\nwaitFor(check,{timeout:10});\nconst close="*/";',
    { line: 2, pattern: 'timeout:', rule: 8 },
  ],
])('reports the sealed scanner refutation %s at its source line', (source, finding) => {
  expect(scan(source)).toEqual([finding]);
  const lines = source.split('\n');
  lines[finding.line - 1] += ' // load-independent: synthetic marked control';
  expect(scan(lines.join('\n'))).toEqual([]);
});

test('keeps real comments, string data and executable findings separate', () => {
  expect(
    scan('/* timeout: 10; test("case", run, 1e3); */\n// expect(Date.now()).toBeLessThan(10);'),
  ).toEqual([]);
  expect(
    scan(
      'const text = "timeout: 10; test(\\"case\\", run, 1e3); /* // load-independent: data */";',
    ),
  ).toEqual([]);
  expect(
    scan(
      'const text="/*";\n// load-independent: synthetic marked control\nwaitFor(check,{timeout:10});\nconst close="*/";',
    ),
  ).toEqual([]);
});

test('the dashboard suite does not depend on machine load', () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), '../..');
  const files = (directory: string): string[] =>
    readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
      const path = join(directory, entry.name);
      return entry.isDirectory() ? files(path) : [path];
    });
  const worklist = [...files(join(root, 'src')), ...files(join(root, 'scripts'))]
    .filter(
      (path) =>
        /\.(?:test|test-utils)\.(?:tsx?|mjs)$/.test(path) ||
        path.startsWith(join(root, 'src/test/')),
    )
    .filter((path) => /\.(?:tsx?|mjs)$/.test(path))
    .flatMap((path) =>
      scan(readFileSync(path, 'utf8')).map(
        (finding) =>
          `${relative(root, path)}:${finding.line}: rule ${finding.rule}: ${finding.pattern}`,
      ),
    );
  expect(
    worklist,
    'Use the shared bound, a condition or fake clock; or add load-independent: <reason>.',
  ).toEqual([]);
});
