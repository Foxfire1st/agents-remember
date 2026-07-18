import assert from "node:assert/strict";
import test from "node:test";

import {
  CLAUDE_SDK_VERSION,
  MAX_SAFE_ERROR_CHARS,
  PI_CODING_AGENT_VERSION,
  PROTOCOL_VERSION,
  buildHandshake,
  parseHelperRequest,
  redactHelperError,
} from "./protocol.js";

test("the exact locked helper versions are protocol constants", () => {
  assert.equal(CLAUDE_SDK_VERSION, "0.3.207");
  assert.equal(PI_CODING_AGENT_VERSION, "0.80.7");
});

test("handshake readiness requires the exact runtime and helper tuple", () => {
  const request = {
    protocolVersion: PROTOCOL_VERSION,
    requestId: "handshake-1",
    operation: "handshake" as const,
    harnessId: "claude" as const,
    expectedRuntimeVersion: "2.1.211",
    expectedHelperVersion: "0.3.207",
  };
  assert.equal(buildHandshake(request, "2.1.211", "0.3.207").status, "ready");
  assert.equal(buildHandshake(request, "2.1.210", "0.3.207").status, "incompatible");
  assert.equal(buildHandshake(request, "2.1.211", "0.3.206").status, "incompatible");
});

test("request parser rejects malformed framing and wrong protocol", () => {
  assert.throws(() => parseHelperRequest("[]"), /JSON object/);
  assert.throws(
    () =>
      parseHelperRequest(
        JSON.stringify({
          protocolVersion: "wrong",
          requestId: "request-1",
          operation: "list",
        }),
      ),
    /version mismatch/,
  );
  assert.throws(
    () =>
      parseHelperRequest(
        JSON.stringify({
          protocolVersion: PROTOCOL_VERSION,
          requestId: "request-1",
          operation: "read",
          harnessId: "claude",
        }),
      ),
    /canonicalProjectScope/,
  );
});

test("request parser rejects unknown fields for every operation", () => {
  const requests = [
    {
      protocolVersion: PROTOCOL_VERSION,
      requestId: "handshake-1",
      operation: "handshake",
      harnessId: "claude",
      expectedRuntimeVersion: "2.1.211",
      expectedHelperVersion: "0.3.207",
    },
    {
      protocolVersion: PROTOCOL_VERSION,
      requestId: "list-1",
      operation: "list",
      harnessId: "claude",
      canonicalProjectScope: "/workspace/project",
      cursor: null,
      limit: 25,
    },
    {
      protocolVersion: PROTOCOL_VERSION,
      requestId: "read-1",
      operation: "read",
      harnessId: "pi",
      vendorConversationId: "conversation-1",
      expectedIdentityDigest: "identity-1",
      canonicalProjectScope: "/workspace/project",
      cursor: null,
      limit: 25,
    },
    {
      protocolVersion: PROTOCOL_VERSION,
      requestId: "resume-1",
      operation: "resolve-resume-target",
      harnessId: "pi",
      vendorConversationId: "conversation-1",
      expectedIdentityDigest: "identity-1",
      canonicalProjectScope: "/workspace/project",
    },
  ];

  for (const request of requests) {
    assert.equal(parseHelperRequest(JSON.stringify(request)).operation, request.operation);
    assert.throws(
      () =>
        parseHelperRequest(
          JSON.stringify({
            ...request,
            NODE_PATH: "/tmp/untrusted",
            authorization: "Bearer secret",
          }),
        ),
      /outside its operation contract/,
    );
  }
});

test("request parser rejects known fields when they belong to another operation", () => {
  const invalidRequests = [
    {
      protocolVersion: PROTOCOL_VERSION,
      requestId: "handshake-1",
      operation: "handshake",
      harnessId: "claude",
      expectedRuntimeVersion: "2.1.211",
      expectedHelperVersion: "0.3.207",
      cursor: null,
      limit: 25,
    },
    {
      protocolVersion: PROTOCOL_VERSION,
      requestId: "list-1",
      operation: "list",
      harnessId: "claude",
      canonicalProjectScope: "/workspace/project",
      cursor: null,
      limit: 25,
      vendorConversationId: "conversation-1",
      expectedIdentityDigest: "identity-1",
    },
    {
      protocolVersion: PROTOCOL_VERSION,
      requestId: "read-1",
      operation: "read",
      harnessId: "pi",
      vendorConversationId: "conversation-1",
      expectedIdentityDigest: "identity-1",
      canonicalProjectScope: "/workspace/project",
      cursor: null,
      limit: 25,
      expectedRuntimeVersion: "0.80.7",
    },
    {
      protocolVersion: PROTOCOL_VERSION,
      requestId: "resume-1",
      operation: "resolve-resume-target",
      harnessId: "pi",
      vendorConversationId: "conversation-1",
      expectedIdentityDigest: "identity-1",
      canonicalProjectScope: "/workspace/project",
      cursor: null,
      limit: 25,
    },
  ];

  for (const request of invalidRequests) {
    assert.throws(
      () => parseHelperRequest(JSON.stringify(request)),
      /outside its operation contract/,
    );
  }
});

test("helper crash detail is fixed allow-listed copy for secrets, paths, and long input", () => {
  const unsafeDetails = [
    "Authorization: Bearer eyJhbGciOi.secret.signature",
    "Bearer opaque-oauth-value",
    '{"token":"plain-secret"}',
    '{"apiKey":"json-secret-value"}',
    "AWS_SECRET_ACCESS_KEY=ABCDEFGHIJKLMNOPQRSTUVWX",
    "ANTHROPIC_API_KEY=anthropic-secret-value",
    "OPENAI_API_KEY=sk-abcdefghijkl",
    "PASSWORD='quoted-secret'",
    "path=/root/private",
    "path=/home/alice/private",
    "path=C:\\Users\\Alice\\private",
    "x".repeat(MAX_SAFE_ERROR_CHARS * 4),
  ];
  const outputs = unsafeDetails.map(redactHelperError);

  assert.equal(new Set(outputs).size, 1);
  for (const output of outputs) {
    assert.equal(output, "helper process failed; raw detail withheld");
    assert.ok(output.length <= MAX_SAFE_ERROR_CHARS);
  }
  for (const unsafe of [
    "eyJhbGciOi.secret.signature",
    "opaque-oauth-value",
    "plain-secret",
    "json-secret-value",
    "ABCDEFGHIJKLMNOPQRSTUVWX",
    "anthropic-secret-value",
    "sk-abcdefghijkl",
    "quoted-secret",
    "/root",
    "alice",
    "Alice",
  ]) {
    assert.equal(outputs.some((output) => output.includes(unsafe)), false);
  }
});
