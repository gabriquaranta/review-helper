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
  readonly line: number;
  readonly column: number;
  readonly metrics: readonly MetricResult[];
}

export interface AnalysisResult {
  readonly file: string;
  readonly metrics: readonly MetricSummary[];
  readonly functions: readonly FunctionResult[];
  readonly error?: string;
}
