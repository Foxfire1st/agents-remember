import type { TaskDocNode } from "../types/projection";

export async function fetchTaskDocument(docPath: string, base = ""): Promise<TaskDocNode> {
  const response = await fetch(`${base}/api/task-document?path=${encodeURIComponent(docPath)}`);
  if (!response.ok) {
    throw new Error(`task document fetch failed: ${response.status}`);
  }
  return (await response.json()) as TaskDocNode;
}
