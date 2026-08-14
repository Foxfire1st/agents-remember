import { readFileSync } from "node:fs";

export const DAGGER_TEST_ATTESTATION_ENV = "AR_DAGGER_TEST_ATTESTATION";
export const DAGGER_TEST_ATTESTATION_PATH = "/tmp/ar-quality/dagger-test-attestation";

export function daggerTestEnvironmentError(
  environ = process.env,
  readAttestation = (path) => readFileSync(path, "utf8"),
) {
  const token = environ[DAGGER_TEST_ATTESTATION_ENV] ?? "";
  if (!/^[0-9a-f]{32}$/.test(token)) {
    return `${DAGGER_TEST_ATTESTATION_ENV} is absent or invalid`;
  }
  let recorded;
  try {
    recorded = readAttestation(DAGGER_TEST_ATTESTATION_PATH);
  } catch (error) {
    return `Dagger attestation file is unavailable: ${String(error)}`;
  }
  if (recorded !== token) {
    return "Dagger environment and attestation-file nonces do not match";
  }
  return null;
}

export function requireDaggerTestEnvironment() {
  const error = daggerTestEnvironmentError();
  if (error !== null) {
    throw new Error(
      `Agents Remember tests are Dagger-only; refusing host execution: ${error}. ` +
        "Run the pinned `dagger call quality ...` graph.",
    );
  }
}
