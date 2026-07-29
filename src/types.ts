export type MetricName =
  | "cyclomatic"
  | "cognitive"
  | "nesting"
  | "functionLength"
  | "parameters";

export interface SourceRange {
  readonly startLine: number;
  readonly startColumn: number;
  readonly endLine: number;
  readonly endColumn: number;
}

export interface MetricResult {
  readonly name: MetricName;
  readonly value: number;
  readonly threshold: number;
  readonly ranges: readonly SourceRange[];
  readonly details: readonly string[];
}

export interface MetricSummary {
  readonly name: MetricName;
  readonly maximum: number;
  readonly average: number;
  readonly threshold: number;
  readonly violatingFunctions: number;
  readonly maximumFunctionId: string | null;
  readonly ranges: readonly SourceRange[];
}

export interface FunctionResult {
  readonly id: string;
  readonly name: string;
  readonly qualifiedName: string;
  readonly uri: string;
  readonly line: number;
  readonly column: number;
  readonly range: SourceRange;
  readonly metrics: readonly MetricResult[];
}

export interface ClassResult {
  readonly id: string;
  readonly qualifiedName: string;
  readonly uri: string;
  readonly line: number;
  readonly column: number;
  readonly range: SourceRange;
}

export interface AnalysisResult {
  readonly file: string;
  readonly metrics: readonly MetricSummary[];
  readonly functions: readonly FunctionResult[];
  readonly classes: readonly ClassResult[];
  readonly error?: string;
}

export type LifecycleNodeRole = "selected" | "entrypoint" | "caller" | "callee" | "both" | "unresolved";

export interface LifecycleNode {
  readonly id: string;
  readonly label: string;
  readonly detail: string;
  readonly uri: string | null;
  readonly line: number | null;
  readonly column: number | null;
  readonly role: LifecycleNodeRole;
  readonly entrypointReason: string | null;
  readonly isTest: boolean;
  readonly kind: "function" | "class" | "unresolved";
}

export type LifecycleEdgeKind = "calls" | "references" | "contains" | "constructs" | "inherits";

export interface LifecycleEdge {
  readonly source: string;
  readonly target: string;
  readonly kind: LifecycleEdgeKind;
}

export interface LifecycleResult {
  readonly selectedSymbolId: string;
  readonly nodes: readonly LifecycleNode[];
  readonly edges: readonly LifecycleEdge[];
  readonly truncated: boolean;
  readonly warnings: readonly string[];
  readonly error: string | null;
}
