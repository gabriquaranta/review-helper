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
    process.stdout.on("data", (chunk: string) => { output += chunk; });
    process.stderr.on("data", (chunk: string) => { errorOutput += chunk; });
    process.on("error", (error: Error) => resolve({ file: document.uri.fsPath, metrics: [], functions: [], error: error.message }));
    process.on("close", (code: number | null) => {
      if (code !== 0) {
        resolve({ file: document.uri.fsPath, metrics: [], functions: [], error: errorOutput.trim() || `Analyzer exited with code ${code ?? "unknown"}.` });
        return;
      }
      try {
        resolve(JSON.parse(output) as AnalysisResult);
      } catch {
        resolve({ file: document.uri.fsPath, metrics: [], functions: [], error: "The analyzer returned invalid JSON." });
      }
    });
    process.stdin.end(JSON.stringify(request));
  });
}
