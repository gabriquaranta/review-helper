import * as vscode from "vscode";
import { AnalysisResult, MetricName, MetricResult, MetricSummary } from "./types";

const metricColors: Record<MetricName, string> = {
  cyclomatic: "#e05252",
  cognitive: "#a855f7",
  nesting: "#3b82f6",
  functionLength: "#22c55e",
  parameters: "#eab308",
};

const metricLabels: Record<MetricName, string> = {
  cyclomatic: "Cyclomatic complexity",
  cognitive: "Cognitive complexity",
  nesting: "Maximum nesting",
  functionLength: "Function length",
  parameters: "Parameters",
};

const metricShortLabels: Record<MetricName, string> = {
  cyclomatic: "Cyclomatic",
  cognitive: "Cognitive",
  nesting: "Nesting",
  functionLength: "Length",
  parameters: "Params",
};

const metricOrder: readonly MetricName[] = ["cyclomatic", "cognitive", "nesting", "functionLength", "parameters"];

export class DashboardProvider implements vscode.WebviewViewProvider {
  private view: vscode.WebviewView | undefined;
  private result: AnalysisResult | undefined;
  private selectedMetric: MetricName | undefined;
  private functionMetricFilter: MetricName | "problematic" | undefined = "problematic";
  private helpMarkdown = "Help is not available yet.";
  private helpVisible = false;

  public resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true };
    view.webview.onDidReceiveMessage((message: { command: string; metric?: MetricName; filter?: MetricName | "problematic"; functionId?: string; line?: number }) => {
      if (message.command === "selectMetric") {
        this.selectedMetric = this.selectedMetric === message.metric ? undefined : message.metric;
        this.render();
        this.onMetricSelected?.(this.selectedMetric);
      }
      if (message.command === "filterFunctions") {
        this.functionMetricFilter = this.functionMetricFilter === message.filter ? undefined : message.filter;
        this.render();
      }
      if (message.command === "copyFunctionReport" && message.functionId && this.result) {
        const fn = this.result.functions.find((item) => item.id === message.functionId);
        if (!fn) return;
        const issues = fn.metrics.filter((metric) => metric.value > metric.threshold);
        const report = [
          `Function report: ${fn.qualifiedName} (line ${fn.line + 1})`,
          "",
          issues.length > 0 ? "Issues:" : "No threshold violations.",
          ...issues.map((metric) => `- ${metricLabels[metric.name]}: ${metric.value} (threshold ${metric.threshold})${metric.details.length > 0 ? ` — ${metric.details.join(", ")}` : ""}`),
        ].join("\n");
        void vscode.env.clipboard.writeText(report).then(() => vscode.window.showInformationMessage("Function report copied to clipboard."));
      }
      if (message.command === "showFunctionLifecycle" && message.functionId && this.result) {
        const fn = this.result.functions.find((item) => item.id === message.functionId);
        if (fn) this.onLifecycleRequested?.(fn.id, fn.qualifiedName);
      }
      if (message.command === "toggleHelp") {
        this.helpVisible = !this.helpVisible;
        this.render();
      }
      if (message.command === "goToLine" && typeof message.line === "number") {
        this.onFunctionSelected?.(message.line);
      }
      if (message.command === "goToMetric" && typeof message.line === "number" && message.metric) {
        this.selectedMetric = message.metric;
        this.render();
        this.onMetricRequested?.(message.metric, message.line);
      }
    });
    this.render();
  }

  public onMetricSelected: ((metric: MetricName | undefined) => void) | undefined;
  public onFunctionSelected: ((line: number) => void) | undefined;
  public onMetricRequested: ((metric: MetricName, line: number) => void) | undefined;
  public onLifecycleRequested: ((functionId: string, qualifiedName: string) => void) | undefined;

  public update(result: AnalysisResult | undefined): void {
    this.result = result;
    this.selectedMetric = undefined;
    this.functionMetricFilter = "problematic";
    this.render();
  }

  public setHelpMarkdown(markdown: string): void {
    this.helpMarkdown = markdown;
    this.render();
  }

  private render(): void {
    if (!this.view) return;
    this.view.webview.html = this.result ? this.content(this.result) : this.emptyContent();
  }

  private emptyContent(): string {
    return "<!doctype html><html><body><p>Open a Python file to view maintainability metrics.</p></body></html>";
  }

  private content(result: AnalysisResult): string {
    const fileName = escapeHtml(result.file.split("/").pop() ?? result.file);
    if (this.helpVisible) return this.helpContent();
    if (result.error) return `<h2>${fileName}</h2><p class="error">${escapeHtml(result.error)}</p>`;
    const metrics = result.metrics.map((metric) => this.metricCard(metric)).join("");
    const filteredFunctions = this.functionMetricFilter === "problematic"
      ? result.functions.filter((fn) => fn.metrics.some((metric) => metric.value > metric.threshold))
      : this.functionMetricFilter
        ? result.functions.filter((fn) => fn.metrics.some((metric) => metric.name === this.functionMetricFilter && metric.value > metric.threshold))
        : result.functions;
    const functions = filteredFunctions.map((fn) => this.functionRow(fn)).join("");
    const functionFilters = [`<button class="function-filter${this.functionMetricFilter === undefined ? " selected" : ""}" data-metric="">All</button>`, `<button class="function-filter${this.functionMetricFilter === "problematic" ? " selected" : ""}" data-metric="problematic">Problematic</button>`, ...metricOrder.map((name) => `<button class="function-filter${this.functionMetricFilter === name ? " selected" : ""}" data-metric="${name}">${metricShortLabels[name]}</button>`)].join("");
    const filterDescription = this.functionMetricFilter === "problematic"
      ? "with at least one metric violation"
      : this.functionMetricFilter
        ? `exceeding ${metricLabels[this.functionMetricFilter]}`
        : "";
    const violations = new Set(result.functions.flatMap((fn) => fn.metrics.filter((metric) => metric.value > metric.threshold).map(() => fn.id))).size;
    const status = violations > 0 ? "Needs attention" : "Within thresholds";
    const statusClass = violations > 0 ? "needs-attention" : "within-thresholds";
    return `<!doctype html><html><head><meta charset="UTF-8"><style>
      body{padding:8px;font-family:var(--vscode-font-family);color:var(--vscode-foreground);font-size:12px}.header{display:flex;align-items:center;justify-content:space-between;gap:8px}h2{font-size:16px;margin:4px 0 10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.help{border:0;background:transparent;color:var(--vscode-textLink-foreground);font:inherit;padding:3px 5px;cursor:pointer}.help:hover{text-decoration:underline}
      .card,.function{display:block;width:100%;box-sizing:border-box;text-align:left;border:1px solid var(--vscode-panel-border);background:var(--vscode-editor-background);color:inherit;padding:7px;margin:4px 0;border-radius:5px;cursor:pointer}.card{border-left:4px solid var(--color)}.card:hover,.function:hover{background:var(--vscode-list-hoverBackground)}.card.selected{border-color:var(--color);background:color-mix(in srgb,var(--color) 10%,var(--vscode-editor-background))}
      .metric{display:flex;justify-content:space-between;align-items:center;gap:8px}.label{padding-left:3px;font-size:13px;font-weight:600}.value{font-weight:600;font-size:12px;text-align:right}.threshold{opacity:.7;font-size:11px;margin-left:3px}.error{color:var(--vscode-errorForeground)}.file-summary{display:flex;justify-content:space-between;align-items:center;margin:0 0 10px;color:var(--vscode-descriptionForeground);font-size:11px}.file-status{font-weight:600}.needs-attention{color:var(--vscode-editorWarning-foreground)}.within-thresholds{color:var(--vscode-testing-iconPassed)}h3{font-size:12px;margin:14px 0 6px;text-transform:uppercase;letter-spacing:.04em;opacity:.8}.function-filters{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:6px}.function-filter{border:1px solid var(--vscode-panel-border);border-radius:4px;background:var(--vscode-editor-background);color:var(--vscode-descriptionForeground);padding:3px 6px;font:inherit;font-size:11px;cursor:pointer}.function-filter:hover,.function-filter.selected{color:var(--vscode-foreground);background:var(--vscode-list-hoverBackground);border-color:var(--vscode-focusBorder)}.filter-summary{color:var(--vscode-descriptionForeground);font-size:11px;margin:4px 0 6px}.function-name{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.badges{display:flex;justify-content:flex-start;gap:3px;flex-wrap:wrap;margin-top:6px}.function-metric{padding:2px 4px;white-space:nowrap;font-size:10px;border-radius:3px;cursor:pointer}.function-metric.violation{border-left:3px solid var(--color);background:color-mix(in srgb,var(--color) 12%,var(--vscode-editor-background));font-weight:600}.function-metric.normal{color:var(--vscode-descriptionForeground);background:var(--vscode-textCodeBlock-background)}.context-menu{position:fixed;display:none;z-index:10;border:1px solid var(--vscode-menu-border);background:var(--vscode-menu-background);box-shadow:0 2px 8px var(--vscode-widget-shadow);padding:2px}.context-menu.visible{display:block}.context-menu button{border:0;background:transparent;color:var(--vscode-menu-foreground);padding:5px 12px;text-align:left;font:inherit;cursor:pointer;white-space:nowrap}.context-menu button:hover{background:var(--vscode-menu-selectionBackground);color:var(--vscode-menu-selectionForeground)}
    </style></head><body><div class="header"><h2 title="${fileName}">${fileName}</h2><button class="help" id="help" title="Explain metrics">?</button></div><div class="file-summary"><span>${result.functions.length} function${result.functions.length === 1 ? "" : "s"} · ${violations} violation${violations === 1 ? "" : "s"}</span><span class="file-status ${statusClass}">${status}</span></div>${metrics}<h3>Functions</h3><div class="function-filters" role="group" aria-label="Filter functions by metric">${functionFilters}</div><p class="filter-summary">Showing ${filteredFunctions.length} of ${result.functions.length} function${result.functions.length === 1 ? "" : "s"}${filterDescription ? ` ${filterDescription}` : ""}.</p>${functions || `<p>No functions match this filter.</p>`}<div class="context-menu" id="context-menu"><button id="show-lifecycle">Show lifecycle</button><button id="copy-report">Copy report</button></div><script>
      const vscode=acquireVsCodeApi();let contextFunctionId;const contextMenu=document.getElementById('context-menu');document.getElementById('help').addEventListener('click',()=>vscode.postMessage({command:'toggleHelp'}));document.querySelectorAll('.card').forEach((card)=>card.addEventListener('click',()=>vscode.postMessage({command:'selectMetric',metric:card.dataset.metric})));document.querySelectorAll('.function-filter').forEach((button)=>button.addEventListener('click',()=>vscode.postMessage({command:'filterFunctions',filter:button.dataset.metric||undefined})));document.querySelectorAll('.function').forEach((button)=>{button.addEventListener('click',()=>vscode.postMessage({command:'goToLine',line:Number(button.dataset.line)}));button.addEventListener('contextmenu',(event)=>{event.preventDefault();contextFunctionId=button.dataset.functionId;contextMenu.style.left=event.clientX+'px';contextMenu.style.top=event.clientY+'px';contextMenu.classList.add('visible');});});document.getElementById('show-lifecycle').addEventListener('click',()=>{vscode.postMessage({command:'showFunctionLifecycle',functionId:contextFunctionId});contextMenu.classList.remove('visible');});document.getElementById('copy-report').addEventListener('click',()=>{vscode.postMessage({command:'copyFunctionReport',functionId:contextFunctionId});contextMenu.classList.remove('visible');});document.addEventListener('click',()=>contextMenu.classList.remove('visible'));document.querySelectorAll('.function-metric').forEach((badge)=>badge.addEventListener('click',(event)=>{event.stopPropagation();vscode.postMessage({command:'goToMetric',metric:badge.dataset.metric,line:Number(badge.dataset.line)});}));
    </script></body></html>`;
  }

  private functionRow(fn: AnalysisResult["functions"][number]): string {
    const badges = metricOrder.map((name) => fn.metrics.find((metric) => metric.name === name)).filter((metric): metric is MetricResult => metric !== undefined).map((metric) => {
      const violation = metric.value > metric.threshold;
      return `<span class="function-metric ${violation ? "violation" : "normal"}" data-metric="${metric.name}" data-line="${fn.line}"${violation ? ` style="--color:${colorForMetric(metric.name)}"` : ""} title="${metricLabels[metric.name]}: ${metric.value} / ${metric.threshold}">${metricShortLabels[metric.name]} ${metric.value}</span>`;
    }).join("");
    const title = fn.metrics.map((metric) => `${metricLabels[metric.name]}: ${metric.value}/${metric.threshold}${metric.details.length > 0 ? ` (${metric.details.join(", ")})` : ""}`).join(" · ");
    return `<button class="function" data-line="${fn.line}" data-function-id="${escapeHtml(fn.id)}" title="${escapeHtml(title)}"><span class="function-name">${escapeHtml(fn.qualifiedName)} · L${fn.line + 1}</span><span class="badges">${badges}</span></button>`;
  }

  private helpContent(): string {
    return `<!doctype html><html><head><meta charset="UTF-8"><style>body{padding:10px;font-family:var(--vscode-font-family);color:var(--vscode-foreground);font-size:12px;line-height:1.45;overflow-x:hidden;overflow-wrap:anywhere}.back{border:1px solid var(--vscode-panel-border);background:var(--vscode-editor-background);color:inherit;padding:5px 8px;cursor:pointer}h1{font-size:16px}h2{font-size:14px}code{background:var(--vscode-textCodeBlock-background);padding:1px 3px}pre{white-space:pre;overflow:auto;background:var(--vscode-textCodeBlock-background);padding:8px}</style></head><body><button class="back" id="back">Back</button>${markdownToHtml(this.helpMarkdown)}<script>const vscode=acquireVsCodeApi();document.getElementById('back').addEventListener('click',()=>vscode.postMessage({command:'toggleHelp'}));</script></body></html>`;
  }

  private metricCard(metric: MetricSummary): string {
    const color = metricColors[metric.name];
    const selected = this.selectedMetric === metric.name;
    return `<button class="card${selected ? " selected" : ""}" data-metric="${metric.name}" aria-pressed="${selected}" style="--color:${color}"><div class="metric"><span class="label">${metricLabels[metric.name]}</span><span class="value">Maximum found: ${metric.maximum}</span></div><span class="threshold">average ${metric.average} · ${metric.violatingFunctions} violating function${metric.violatingFunctions === 1 ? "" : "s"} · threshold ${metric.threshold}${selected ? " · selected" : ""}</span></button>`;
  }
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character] ?? character);
}

function markdownToHtml(markdown: string): string {
  const lines = markdown.split("\n");
  const output: string[] = [];
  const codeLines: string[] = [];
  let inCodeBlock = false;
  let inList = false;

  const closeList = (): void => {
    if (inList) {
      output.push("</ul>");
      inList = false;
    }
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      closeList();
      if (inCodeBlock) {
        output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines.length = 0;
      }
      inCodeBlock = !inCodeBlock;
      continue;
    }
    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }
    if (line.startsWith("### ")) {
      closeList();
      output.push(`<h3>${inlineMarkdown(line.slice(4))}</h3>`);
    } else if (line.startsWith("## ")) {
      closeList();
      output.push(`<h2>${inlineMarkdown(line.slice(3))}</h2>`);
    } else if (line.startsWith("# ")) {
      closeList();
      output.push(`<h1>${inlineMarkdown(line.slice(2))}</h1>`);
    } else if (line.startsWith("- ")) {
      if (!inList) {
        output.push("<ul>");
        inList = true;
      }
      output.push(`<li>${inlineMarkdown(line.slice(2))}</li>`);
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList();
      output.push(`<p>${inlineMarkdown(line)}</p>`);
    }
  }
  if (inCodeBlock) output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  closeList();
  return output.join("");
}

function inlineMarkdown(value: string): string {
  return escapeHtml(value).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

export function colorForMetric(metric: MetricName): string { return metricColors[metric]; }
