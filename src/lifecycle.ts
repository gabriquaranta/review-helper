import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import * as path from "node:path";
import * as vscode from "vscode";
import { LifecycleResult } from "./types";

interface LifecycleSourceFile {
  readonly path: string;
  readonly workspaceRoot: string;
  readonly module: string;
  readonly source: string;
}

interface LifecycleRequest {
  readonly files: readonly LifecycleSourceFile[];
  readonly selectedFunctionId: string;
  readonly maxNodes: number;
}

const defaultExclusions: readonly string[] = [
  "**/.git/**",
  "**/.venv/**",
  "**/venv/**",
  "**/node_modules/**",
  "**/__pycache__/**",
  "**/build/**",
  "**/dist/**",
  "**/generated/**",
  "**/*.generated.py",
  "**/*_pb2.py",
];

/**
 * Analyze the selected function against Python sources in the current workspace.
 *
 * Lifecycle analysis stays on demand so editing a document never triggers a workspace scan.
 */
export async function analyzeLifecycle(
  selectedFunctionId: string,
  pythonPath: string,
  extensionPath: string,
): Promise<LifecycleResult> {
  const workspaceFolders = vscode.workspace.workspaceFolders;
  if (!workspaceFolders || workspaceFolders.length === 0) {
    return emptyResult(selectedFunctionId, "Open a workspace folder to analyze a function lifecycle.");
  }
  const exclude = exclusionGlob();
  const uris = await vscode.workspace.findFiles("**/*.py", exclude, 2001);
  if (uris.length > 2000) {
    return emptyResult(selectedFunctionId, "Lifecycle analysis is limited to workspaces containing at most 2,000 Python files.");
  }
  const openDocuments = new Map(
    vscode.workspace.textDocuments
      .filter((document) => document.languageId === "python")
      .map((document) => [document.uri.toString(), document]),
  );
  const projectMarkers = await vscode.workspace.findFiles("**/{pyproject.toml,setup.cfg,setup.py}", exclude, 500);
  const projectRoots = [...new Set(projectMarkers.map((uri) => path.dirname(uri.fsPath)))];
  const sourcePaths = new Set(uris.map((uri) => uri.fsPath));
  const files = await Promise.all(uris.map(async (uri): Promise<LifecycleSourceFile> => {
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (!folder) throw new Error(`Python source is outside the workspace: ${uri.fsPath}`);
    const projectRoot = nearestProjectRoot(uri.fsPath, projectRoots) ?? folder.uri.fsPath;
    const openDocument = openDocuments.get(uri.toString());
    const source = openDocument?.getText() ?? new TextDecoder().decode(await vscode.workspace.fs.readFile(uri));
    return {
      path: uri.fsPath,
      workspaceRoot: projectRoot,
      module: moduleName(projectRoot, uri.fsPath, sourcePaths.has(path.join(projectRoot, "src", "__init__.py"))),
      source,
    };
  }));
  const request: LifecycleRequest = {
    files,
    selectedFunctionId,
    maxNodes: 100,
  };
  return runAnalyzer(request, pythonPath, extensionPath);
}

/**
 * Build the explicit exclusion glob used by the workspace scan.
 *
 * Combining built-in and enabled VS Code exclusions prevents dependency and generated trees from leaking in.
 */
function exclusionGlob(): string {
  const configured = ["files.exclude", "search.exclude"].flatMap((section) => {
    const values = vscode.workspace.getConfiguration().get<Record<string, boolean>>(section, {});
    return Object.entries(values).filter(([, enabled]) => enabled).map(([pattern]) => pattern);
  });
  const patterns = [...new Set([...defaultExclusions, ...configured])];
  return patterns.length === 1 ? patterns[0] : `{${patterns.join(",")}}`;
}

/**
 * Convert a workspace-relative Python path into its importable module name.
 *
 * Matching Python's file-based module convention lets explicit imports resolve without name guessing.
 */
function moduleName(workspaceRoot: string, filePath: string, srcIsPackage: boolean): string {
  const relative = path.relative(workspaceRoot, filePath).replaceAll(path.sep, "/");
  const importRelative = relative.startsWith("src/") && !srcIsPackage ? relative.slice("src/".length) : relative;
  const withoutExtension = importRelative.slice(0, -3);
  const withoutPackageInitializer = withoutExtension.endsWith("/__init__")
    ? withoutExtension.slice(0, -"/__init__".length)
    : withoutExtension === "__init__"
      ? path.basename(workspaceRoot)
      : withoutExtension;
  return withoutPackageInitializer.replaceAll("/", ".");
}

/**
 * Find the innermost Python project containing a source file.
 *
 * Project metadata boundaries prevent sibling repositories in one VS Code workspace from corrupting module names.
 */
function nearestProjectRoot(filePath: string, projectRoots: readonly string[]): string | undefined {
  return projectRoots
    .filter((root) => {
      const relative = path.relative(root, filePath);
      return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
    })
    .sort((left, right) => right.length - left.length)[0];
}

/**
 * Execute the isolated Python lifecycle analyzer.
 *
 * A one-shot process uses the extension's existing configured interpreter and cannot retain application state.
 */
function runAnalyzer(
  request: LifecycleRequest,
  pythonPath: string,
  extensionPath: string,
): Promise<LifecycleResult> {
  const script = path.join(extensionPath, "src", "lifecycle.py");
  return new Promise<LifecycleResult>((resolve) => {
    const process: ChildProcessWithoutNullStreams = spawn(pythonPath, [script], {
      stdio: ["pipe", "pipe", "pipe"],
    });
    let output = "";
    let errorOutput = "";
    process.stdout.setEncoding("utf8");
    process.stderr.setEncoding("utf8");
    process.stdout.on("data", (chunk: string) => { output += chunk; });
    process.stderr.on("data", (chunk: string) => { errorOutput += chunk; });
    process.on("error", (error: Error) => resolve(emptyResult(request.selectedFunctionId, error.message)));
    process.on("close", (code: number | null) => {
      if (code !== 0) {
        resolve(emptyResult(
          request.selectedFunctionId,
          errorOutput.trim() || `Lifecycle analyzer exited with code ${code ?? "unknown"}.`,
        ));
        return;
      }
      try {
        resolve(JSON.parse(output) as LifecycleResult);
      } catch {
        resolve(emptyResult(request.selectedFunctionId, "The lifecycle analyzer returned invalid JSON."));
      }
    });
    process.stdin.end(JSON.stringify(request));
  });
}

/**
 * Create a typed lifecycle failure result.
 *
 * Returning failures through the normal contract lets the graph panel render a stable error state.
 */
function emptyResult(selectedFunctionId: string, error: string): LifecycleResult {
  return {
    selectedFunctionId,
    nodes: [],
    edges: [],
    truncated: false,
    warnings: [],
    error,
  };
}
