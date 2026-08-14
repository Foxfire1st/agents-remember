import { describe, expect, it } from "vitest";
import {
  DAGGER_TEST_ATTESTATION_ENV,
  daggerTestEnvironmentError,
} from "./require-dagger-test-environment.mjs";

const TOKEN = "0123456789abcdef0123456789abcdef";

describe("Dagger test environment guard", () => {
  it("refuses a missing Dagger nonce", () => {
    expect(daggerTestEnvironmentError({}, () => TOKEN)).toMatch(/absent or invalid/);
  });

  it("refuses a mismatched attestation file", () => {
    expect(
      daggerTestEnvironmentError({ [DAGGER_TEST_ATTESTATION_ENV]: TOKEN }, () => "f".repeat(32)),
    ).toMatch(/do not match/);
  });

  it("accepts the matching per-run Dagger nonce", () => {
    expect(
      daggerTestEnvironmentError({ [DAGGER_TEST_ATTESTATION_ENV]: TOKEN }, () => TOKEN),
    ).toBeNull();
  });
});
