import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import * as path from "node:path";
import * as vscode from "vscode";
import { AnalysisResult } from "./types";

interface AnalyzerRequest {
  readonly source: string;
  readonly file: string;
  readonly thresholds: Record<string, number>;
}

export async function analyzeDocument(
  document: vscode.TextDocument,
  pythonPath: string,
  thresholds: Record<string, number>,
  extensionPath: string,
  signal: AbortSignal,
): Promise<AnalysisResult> {
  const request: AnalyzerRequest = {
    source: document.getText(),
    file: document.uri.fsPath,
    thresholds,
  };
  const script = path.join(extensionPath, "src", "analyzer.py");

  return new Promise<AnalysisResult>((resolve) => {
    const process: ChildProcessWithoutNullStreams = spawn(pythonPath, [script], {
      stdio: ["pipe", "pipe", "pipe"],
    });
    let output = "";
    let errorOutput = "";
    process.stdout.setEncoding("utf8");
    process.stderr.setEncoding("utf8");
    /** Stop the analyzer subprocess when its result is obsolete.
     * Cancelling stale work keeps rapid editor changes from building a process backlog.
     */
    const cancel = (): void => { process.kill(); };
    process.stdout.on("data", (chunk: string) => { output += chunk; });
    process.stderr.on("data", (chunk: string) => { errorOutput += chunk; });
    process.on("error", (error: Error) => {
      signal.removeEventListener("abort", cancel);
      resolve({ file: document.uri.fsPath, metrics: [], functions: [], classes: [], error: error.message });
    });
    process.on("close", (code: number | null) => {
      signal.removeEventListener("abort", cancel);
      if (signal.aborted) {
        resolve({ file: document.uri.fsPath, metrics: [], functions: [], classes: [], error: "Analysis cancelled." });
        return;
      }
      if (code !== 0) {
        resolve({ file: document.uri.fsPath, metrics: [], functions: [], classes: [], error: errorOutput.trim() || `Analyzer exited with code ${code ?? "unknown"}.` });
        return;
      }
      try {
        resolve(JSON.parse(output) as AnalysisResult);
      } catch {
        resolve({ file: document.uri.fsPath, metrics: [], functions: [], classes: [], error: "The analyzer returned invalid JSON." });
      }
    });
    signal.addEventListener("abort", cancel, { once: true });
    if (signal.aborted) {
      cancel();
      return;
    }
    process.stdin.end(JSON.stringify(request));
  });
}
