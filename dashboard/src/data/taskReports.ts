// Same-origin client for a task's recorded role starts (GET /api/role-launch/reports).
// The rows come from what the product recorded at each start, newest first, at most
// twenty — never from a walk of the notes tree. An unreadable record rejects so the
// page can say the reports could not be listed while keeping its Notes link.
import { getJson, qs } from "./files";

export interface TaskReportRow {
  role: string;
  requestId: string;
  createdAt: string;
  status: string | null;
  path: string | null;
  canonicalPath: string | null;
  available: boolean;
}
export interface TaskReports {
  repo: string;
  master: string;
  doc: string;
  reports: TaskReportRow[];
  total: number;
  explorer: string | null; // notes-relative folder of the report files, set only when more exist
}

export const listTaskReports = (
  repo: string,
  master: string,
  doc: string,
  base = "",
  signal?: AbortSignal,
): Promise<TaskReports> =>
  getJson<TaskReports>(`${base}/api/role-launch/reports?${qs({ repo, master, doc })}`, undefined, signal);

// Dashboard doc paths carry the "/tasks/<repo>/" prefix while recorded task references
// are root-relative inside the repository task root; the reader scope must name the
// recorded form so the report resolves to this task's recorded start.
export const docRefPath = (repo: string, docPath: string): string => {
  const prefix = `/tasks/${repo}/`;
  return docPath.startsWith(prefix) ? docPath.slice(prefix.length) : docPath;
};
