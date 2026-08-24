export const DAGGER_TEST_ATTESTATION_ENV: string;
export const DAGGER_TEST_ATTESTATION_PATH: string;

export function daggerTestEnvironmentError(
  environ?: NodeJS.ProcessEnv,
  readAttestation?: (path: string) => string,
): string | null;

export function requireDaggerTestEnvironment(subject?: string): void;
