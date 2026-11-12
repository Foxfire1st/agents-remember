import type { RoleDocumentScope } from "../cockpit/roleLaunchModel";

export interface RoleReportContent {
  path: string;
  language: string;
  size: number;
  truncated: boolean;
  content: string;
}

export async function readRoleReport(selection: RoleDocumentScope, requestId: string): Promise<RoleReportContent> {
  let response: Response;
  try {
    response = await fetch("/api/role-launch/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...selection, requestId }),
    });
  } catch {
    throw new Error("The report could not be opened. Check the dashboard connection and retry.");
  }
  const value = await response.json().catch(() => null) as (RoleReportContent & { detail?: unknown }) | null;
  if (!response.ok || !value) {
    const reason = typeof value?.detail === "string" ? ": " + value.detail : ".";
    throw new Error("The report could not be opened (HTTP " + response.status + ")" + reason);
  }
  return value as RoleReportContent;
}
