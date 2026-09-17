import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { isAbsolute, resolve, sep } from "node:path";

/**
 * Read and verify the Agents Remember capsule carrier this runtime was launched with.
 *
 * The launch line is the only thing that says which carrier belongs to this runtime, and the
 * carrier is the only thing that says which instructions, task facts and write surfaces the seat
 * admitted. Everything here therefore fails closed and by name: an unbound launch, a partly
 * declared binding, a carrier whose bytes do not match the declared digest, a carrier written for
 * another binding, or a carrier whose workspace is not the launch's workspace each produce a
 * distinct error code rather than a plausible-looking default.
 *
 * The module is deliberately dependency-free apart from Node's standard library, so an operator (or
 * a test) can execute it directly without compiling the agent application.
 */

export const CARRIER_SCHEMA = "ar-eve-capsule-carrier/v1";
export const BINDING_REF_ENV = "AR_BINDING_REF";
export const CAPSULE_PATH_ENV = "AR_CAPSULE_PATH";
export const CAPSULE_DIGEST_ENV = "AR_CAPSULE_DIGEST";
export const WORKSPACE_ROOT_ENV = "AR_WORKSPACE_ROOT";

export class ArCapsuleError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ArCapsuleError";
    this.code = code;
  }
}

export interface ArCapsuleIdentity {
  readonly role: string;
  readonly taskReference: string;
  readonly operation: string;
  readonly bindingRef: string;
  readonly semanticDigest: string;
}

export interface ArCapsuleWorkspace {
  readonly root: string;
  readonly repositoryId: string;
  readonly workBranch: string;
  readonly baseCommit: string;
  readonly contractPath: string;
}

export interface ArWriteScope {
  readonly kind: string;
  readonly path: string;
  readonly root: string;
}

export interface ArLaunchBinding {
  readonly bindingRef: string;
  readonly capsulePath: string;
  readonly capsuleDigest: string;
  readonly workspaceRoot: string;
}

export interface ArCapsule {
  readonly schema: string;
  readonly identity: ArCapsuleIdentity;
  readonly workspace: ArCapsuleWorkspace;
  readonly instructions: readonly string[];
  readonly instructionIdentities: readonly string[];
  readonly instructionDigests: readonly string[];
  readonly instructionText: string;
  readonly taskContextMarkdown: string;
  readonly taskContextDigest: string;
  readonly writeScopes: readonly ArWriteScope[];
  readonly grantedTools: readonly string[];
  readonly carryForward: readonly string[];
  readonly carrierPath: string;
  readonly carrierDigest: string;
}

/** The binding one launch declares, or `null` when it declares none. */
export function launchBinding(env: NodeJS.ProcessEnv | Record<string, string | undefined>): ArLaunchBinding | null {
  const bindingRef = env[BINDING_REF_ENV];
  const capsulePath = env[CAPSULE_PATH_ENV];
  const capsuleDigest = env[CAPSULE_DIGEST_ENV];
  const workspaceRoot = env[WORKSPACE_ROOT_ENV];
  const declared = [bindingRef, capsulePath, capsuleDigest, workspaceRoot].filter(
    (value) => value !== undefined && value !== "",
  );
  if (declared.length === 0) {
    return null;
  }
  if (declared.length !== 4) {
    throw new ArCapsuleError(
      "partial-binding",
      "this launch declares part of an Agents Remember binding; a bound launch names all four of " +
        `${BINDING_REF_ENV}, ${CAPSULE_PATH_ENV}, ${CAPSULE_DIGEST_ENV} and ${WORKSPACE_ROOT_ENV}`,
    );
  }
  return {
    bindingRef: bindingRef as string,
    capsulePath: capsulePath as string,
    capsuleDigest: capsuleDigest as string,
    workspaceRoot: workspaceRoot as string,
  };
}

/** The verified capsule for this launch. Throws {@link ArCapsuleError} for every refusal. */
export function loadVerifiedCapsule(
  env: NodeJS.ProcessEnv | Record<string, string | undefined>,
): ArCapsule {
  const binding = launchBinding(env);
  if (binding === null) {
    throw new ArCapsuleError(
      "unbound-launch",
      "this runtime was launched without an Agents Remember binding, so it has no admitted seat, " +
        "no capsule instructions and no admitted workspace; refusing to execute",
    );
  }
  const payload = readCarrierBytes(binding.capsulePath);
  const observedDigest = digestOf(payload);
  if (observedDigest !== binding.capsuleDigest) {
    throw new ArCapsuleError(
      "carrier-digest-mismatch",
      `the carrier at ${binding.capsulePath} is ${observedDigest}, not the declared ${binding.capsuleDigest}`,
    );
  }
  const carrier = parseCarrier(new TextDecoder().decode(payload), binding.capsulePath, observedDigest);
  if (carrier.identity.bindingRef !== binding.bindingRef) {
    throw new ArCapsuleError(
      "carrier-binding-mismatch",
      `the carrier belongs to binding ${carrier.identity.bindingRef}, not the launched ${binding.bindingRef}`,
    );
  }
  const declaredRoot = resolve(binding.workspaceRoot);
  if (resolve(carrier.workspace.root) !== declaredRoot) {
    throw new ArCapsuleError(
      "carrier-workspace-mismatch",
      `the carrier admits workspace ${carrier.workspace.root}, but this launch runs in ${declaredRoot}`,
    );
  }
  return carrier;
}

/**
 * Admit one path for reading inside the admitted workspace.
 *
 * Reading is confined to the workspace root exactly as the launch declares it, so a tool cannot
 * read the coordination root, a sibling worktree, or the carrier itself.
 */
export function admitWorkspaceRead(capsule: ArCapsule, path: string): string {
  return confine(capsule.workspace.root, path, capsule.workspace.root);
}

/**
 * Admit one path for writing, against the scopes the admitted capsule declares.
 *
 * One rule serves every scope: a target is writable when it resolves inside `resolve(root, path)`.
 * A workspace scope names the admitted worktree; an absolute scope names a surface AR admitted by
 * its own path, such as the seat's report directory. Nothing outside all scopes is writable, so a
 * sibling task's worktree and a read-only memory surface are refused by the same rule that admits
 * the seat's own report.
 */
export function admitWritePath(capsule: ArCapsule, path: string): string {
  const target = isAbsolute(path) ? resolve(path) : resolve(capsule.workspace.root, path);
  for (const scope of capsule.writeScopes) {
    const base = resolve(scope.root, scope.path);
    if (target.startsWith(base + sep)) {
      return target;
    }
  }
  const admitted = capsule.writeScopes.map((scope) => resolve(scope.root, scope.path)).join(", ");
  throw new ArCapsuleError(
    "write-scope-refused",
    `path ${JSON.stringify(path)} resolves to ${target}, which is outside every surface this ` +
      `seat was admitted to write (${admitted})`,
  );
}

/** The admitted write surfaces, for an operator or a test to print. */
export function admittedWriteSurfaces(capsule: ArCapsule): readonly string[] {
  return capsule.writeScopes.map((scope) => resolve(scope.root, scope.path));
}

function confine(root: string, path: string, label: string): string {
  const rootPath = resolve(root);
  const candidate = isAbsolute(path) ? resolve(path) : resolve(rootPath, path);
  if (!candidate.startsWith(rootPath + sep)) {
    throw new ArCapsuleError(
      "path-escape",
      `path ${JSON.stringify(path)} is outside ${label}`,
    );
  }
  return candidate;
}

function readCarrierBytes(capsulePath: string): Buffer {
  try {
    return readFileSync(capsulePath);
  } catch (error) {
    throw new ArCapsuleError(
      "carrier-missing",
      `the declared capsule carrier ${capsulePath} could not be read: ${(error as Error).message}`,
    );
  }
}

function digestOf(payload: Buffer): string {
  return `sha256:${createHash("sha256").update(payload).digest("hex")}`;
}

function parseCarrier(text: string, carrierPath: string, carrierDigest: string): ArCapsule {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    throw new ArCapsuleError(
      "carrier-unreadable",
      `the carrier at ${carrierPath} is not JSON: ${(error as Error).message}`,
    );
  }
  const payload = asObject(raw, "carrier");
  const schema = requireText(payload, "schema", "carrier");
  if (schema !== CARRIER_SCHEMA) {
    throw new ArCapsuleError(
      "carrier-schema",
      `the carrier schema ${JSON.stringify(schema)} is not ${JSON.stringify(CARRIER_SCHEMA)}`,
    );
  }
  const identityPayload = asObject(payload.identity, "carrier identity");
  const workspacePayload = asObject(payload.workspace, "carrier workspace");
  const identity: ArCapsuleIdentity = {
    role: requireText(identityPayload, "role", "carrier identity"),
    taskReference: requireText(identityPayload, "taskReference", "carrier identity"),
    operation: requireText(identityPayload, "operation", "carrier identity"),
    bindingRef: requireText(identityPayload, "bindingRef", "carrier identity"),
    semanticDigest: requireText(identityPayload, "semanticDigest", "carrier identity"),
  };
  const workspace: ArCapsuleWorkspace = {
    root: requireText(workspacePayload, "root", "carrier workspace"),
    repositoryId: requireText(workspacePayload, "repositoryId", "carrier workspace"),
    workBranch: requireText(workspacePayload, "workBranch", "carrier workspace"),
    baseCommit: requireText(workspacePayload, "baseCommit", "carrier workspace"),
    contractPath: requireText(workspacePayload, "contractPath", "carrier workspace"),
  };
  const instructions = textList(payload, "instructions");
  const instructionIdentities = textList(payload, "instructionIdentities");
  const instructionDigests = textList(payload, "instructionDigests");
  if (instructions.length === 0) {
    throw new ArCapsuleError(
      "carrier-empty-instructions",
      "the carrier carries no instruction block; a bound seat always has at least one, so an empty " +
        "carrier is a defect rather than an unbound seat",
    );
  }
  if (
    instructions.length !== instructionIdentities.length ||
    instructions.length !== instructionDigests.length
  ) {
    throw new ArCapsuleError(
      "carrier-instruction-mismatch",
      `the carrier declares ${instructions.length} instruction blocks, ` +
        `${instructionIdentities.length} identities and ${instructionDigests.length} digests`,
    );
  }
  const writeScopes = scopeList(payload);
  const workspaceScopes = writeScopes.filter((scope) => scope.kind === "workspace");
  if (workspaceScopes.length !== 1) {
    throw new ArCapsuleError(
      "carrier-scope-mismatch",
      `the carrier must declare exactly one workspace write scope; it declares ${workspaceScopes.length}`,
    );
  }
  if (resolve(workspaceScopes[0].root) !== resolve(workspace.root)) {
    throw new ArCapsuleError(
      "carrier-scope-mismatch",
      `the carrier's workspace scope root ${workspaceScopes[0].root} is not its workspace ${workspace.root}`,
    );
  }
  return {
    schema,
    identity,
    workspace,
    instructions,
    instructionIdentities,
    instructionDigests,
    instructionText: instructions.join(""),
    taskContextMarkdown: optionalText(payload, "taskContextMarkdown"),
    taskContextDigest: optionalText(payload, "taskContextDigest"),
    writeScopes,
    grantedTools: textList(payload, "grantedTools"),
    carryForward: textList(payload, "carryForward"),
    carrierPath,
    carrierDigest,
  };
}

function scopeList(payload: Record<string, unknown>): ArWriteScope[] {
  const raw = payload.writeScopes;
  if (!Array.isArray(raw)) {
    throw new ArCapsuleError("carrier-shape", "carrier writeScopes must be a list");
  }
  return raw.map((item) => {
    const scope = asObject(item, "write scope");
    const kind = requireText(scope, "kind", "write scope");
    if (kind !== "workspace" && kind !== "absolute") {
      throw new ArCapsuleError("carrier-shape", `carrier write scope has unknown kind ${JSON.stringify(kind)}`);
    }
    return {
      kind,
      path: requireText(scope, "path", "write scope"),
      root: requireText(scope, "root", "write scope"),
    };
  });
}

function asObject(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ArCapsuleError("carrier-shape", `carrier ${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function requireText(payload: Record<string, unknown>, key: string, label: string): string {
  const value = payload[key];
  if (typeof value !== "string" || value === "") {
    throw new ArCapsuleError("carrier-shape", `carrier ${label} requires a non-empty ${key}`);
  }
  return value;
}

function optionalText(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value !== "string") {
    throw new ArCapsuleError("carrier-shape", `carrier ${key} must be a string when present`);
  }
  return value;
}

function textList(payload: Record<string, unknown>, key: string): string[] {
  const value = payload[key];
  if (value === undefined || value === null) {
    return [];
  }
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new ArCapsuleError("carrier-shape", `carrier ${key} must be a list of strings`);
  }
  return value as string[];
}
