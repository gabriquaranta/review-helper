import * as vscode from "vscode";
import { readFile } from "node:fs/promises";
import * as path from "node:path";
import { analyzeDocument } from "./analyzer";
import { DashboardProvider } from "./dashboard";
import { AnalysisResult, MetricName, SourceRange } from "./types";

const metricNames: readonly MetricName[] = ["cyclomatic", "cognitive", "nesting", "functionLength", "parameters"];

export function activate(context: vscode.ExtensionContext): void {
  const dashboard = new DashboardProvider();
  void readFile(path.join(context.extensionPath, "METRICS.md"), "utf8").then((markdown) => dashboard.setHelpMarkdown(markdown));
  context.subscriptions.push(vscode.window.registerWebviewViewProvider("pythonMaintainability.dashboard", dashboard));
  const decorations = new Map<MetricName, vscode.TextEditorDecorationType>();
  for (const metric of metricNames) {
    decorations.set(metric, vscode.window.createTextEditorDecorationType({
      isWholeLine: false,
      backgroundColor: `${metricColor(metric)}14`,
      overviewRulerColor: metricColor(metric),
      overviewRulerLane: vscode.OverviewRulerLane.Right,
    }));
  }
  context.subscriptions.push(...decorations.values());

  let version = 0;
  let lastResult: AnalysisResult | undefined;
  const refresh = async (): Promise<void> => {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.languageId !== "python") {
      dashboard.update(undefined);
      clearDecorations(decorations);
      return;
    }
    const currentVersion = ++version;
    const config = vscode.workspace.getConfiguration("pythonMaintainability");
    const result = await analyzeDocument(editor.document, config.get<string>("pythonPath", "python3"), {
      cyclomatic: config.get<number>("cyclomaticThreshold", 10),
      cognitive: config.get<number>("cognitiveThreshold", 10),
      nesting: config.get<number>("nestingThreshold", 3),
      functionLength: config.get<number>("functionLengthThreshold", 50),
      parameters: config.get<number>("parameterThreshold", 5),
    }, context.extensionPath);
    if (currentVersion !== version || vscode.window.activeTextEditor?.document !== editor.document) return;
    lastResult = result;
    dashboard.update(result);
    applyDecorations(editor, result, decorations);
  };
  dashboard.onMetricSelected = (metric) => {
    applyDecorations(vscode.window.activeTextEditor, lastResult, decorations, metric);
    if (metric) navigateToMetric(lastResult, metric, undefined);
  };
  dashboard.onMetricRequested = (metric, line) => {
    applyDecorations(vscode.window.activeTextEditor, lastResult, decorations, metric);
    navigateToMetric(lastResult, metric, line);
  };
  dashboard.onFunctionSelected = (line) => {
    const editor = vscode.window.activeTextEditor;
    if (editor) { editor.selection = new vscode.Selection(line, 0, line, 0); editor.revealRange(new vscode.Range(line, 0, line, 0)); }
  };
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(() => { void refresh(); }),
    vscode.workspace.onDidChangeTextDocument((event) => { if (event.document === vscode.window.activeTextEditor?.document) void refresh(); }),
    vscode.workspace.onDidChangeConfiguration((event) => { if (event.affectsConfiguration("pythonMaintainability")) void refresh(); }),
    vscode.commands.registerCommand("pythonMaintainability.refresh", () => void refresh()),
  );
  void refresh();
}

function applyDecorations(editor: vscode.TextEditor | undefined, result: AnalysisResult | undefined, decorations: Map<MetricName, vscode.TextEditorDecorationType>, selected?: MetricName): void {
  if (!editor || !result || result.error) { clearDecorations(decorations); return; }
  for (const metric of metricNames) {
    const ranges = selected && selected !== metric ? [] : result.functions.flatMap((fn) => fn.metrics.filter((item) => item.name === metric && item.value > item.threshold).flatMap((item) => item.ranges.map(toRange)));
    editor.setDecorations(decorations.get(metric) as vscode.TextEditorDecorationType, ranges);
  }
}

function navigateToMetric(result: AnalysisResult | undefined, metric: MetricName, line: number | undefined): void {
  const editor = vscode.window.activeTextEditor;
  if (!editor || !result || result.error) return;
  const candidates = result.functions.filter((fn) => line === undefined || fn.line === line);
  const functions = candidates.length > 0 ? candidates : result.functions;
  if (functions.length === 0) return;
  const target = functions.reduce((current, fn) => {
    const currentMetric = current.metrics.find((item) => item.name === metric);
    const nextMetric = fn.metrics.find((item) => item.name === metric);
    return nextMetric && (!currentMetric || nextMetric.value > currentMetric.value) ? fn : current;
  }, functions[0]);
  const range = target?.metrics.find((item) => item.name === metric)?.ranges[0];
  if (!range) return;
  const position = new vscode.Position(range.startLine, range.startColumn);
  editor.selection = new vscode.Selection(position, position);
  editor.revealRange(new vscode.Range(position, position), vscode.TextEditorRevealType.InCenterIfOutsideViewport);
}

function toRange(range: SourceRange): vscode.Range { return new vscode.Range(range.startLine, range.startColumn, range.endLine, range.endColumn); }
function clearDecorations(decorations: Map<MetricName, vscode.TextEditorDecorationType>): void { for (const decoration of decorations.values()) void vscode.window.activeTextEditor?.setDecorations(decoration, []); }
function metricColor(metric: MetricName): string { return ({ cyclomatic: "#e05252", cognitive: "#a855f7", nesting: "#3b82f6", functionLength: "#22c55e", parameters: "#eab308" })[metric]; }
