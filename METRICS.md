# Python Maintainability Metrics

The extension analyzes the currently active Python editor, including unsaved changes.

## Dashboard values

The file-level dashboard shows the highest value found among the functions in the active file.

### Cyclomatic complexity

Cyclomatic complexity starts at `1` and adds one for each independent control-flow path:

- `if`
- `for` and `while`
- exception handlers
- conditional expressions
- `with` and `assert`
- loop `else` and `try` `else` paths
- comprehensions and their filters
- additional boolean conditions such as `and` and `or`
- non-default `match` cases

Wildcard `case _` is treated like an unconditional `else` and does not add a decision.

Example:

```python
def example(value):
    if value and value > 10:
        return True
    return False
```

Approximate score: `1` base + `1` for `if` + `1` for the additional boolean condition = `3`.

Default threshold: `10`.

### Cognitive complexity

Cognitive complexity is a Sonar-style estimate of how difficult control flow is for a person to follow. Breaks in linear flow add points, and nested control-flow structures add additional points. `try`, `finally`, and `with` wrappers do not add points; exception handlers, loops, conditionals, logical-operator sequences, recursion, `break`, and `continue` do.

Deeply nested code therefore scores higher than equivalent flat guard clauses.

Default threshold: `10`.

### Maximum nesting

This reports the deepest actual nested control-flow block. Nested functions and classes are measured independently and do not inflate their parent function.

```python
if active:                  # 1
    for item in items:      # 2
        if item.valid:      # 3
            process(item)
```

The value is `3`.

Default threshold: `3`.

### Function length

This counts source lines containing code from the function declaration to the end of the function. Blank lines, comments, leading docstrings, and nested function or class bodies are excluded.

Default threshold: `50`.

### Parameters

This counts explicit positional-only, normal positional, and keyword-only parameters, excluding a `self` or `cls` receiver on methods. `*args` and `**kwargs` are reported explicitly as parameter details rather than silently omitted.

Default threshold: `5`.

## Function list

The function list contains every function in the active file. Functions use qualified names and line numbers, such as `outer.<locals>.visit · L58`, so duplicate local names remain distinguishable.

The list defaults to `Problematic`, showing functions with at least one metric violation. Use the metric buttons above the list to show only functions that exceed a selected metric threshold, or select `All` to restore the complete function list.

Right-click a function and choose `Copy report` to copy its threshold violations, values, and thresholds to the clipboard.

Each file-level metric reports:

- `Maximum found`: the highest value among functions.
- `Average`: the arithmetic mean across functions, rounded to one decimal place.
- `Violating functions`: the number of functions whose value exceeds the threshold.

## Editor highlights

A function is highlighted only when a metric exceeds its threshold:

```text
value > threshold
```

Metric colors are stable by metric:

- Red: cyclomatic complexity
- Purple: cognitive complexity
- Blue: nesting depth
- Green: function length
- Yellow: parameter count

Clicking a metric hides the other decorations and shows only that metric's violations.

The current version highlights the whole offending function. Future refinements can target signatures, branches, and nested blocks more precisely.

## Configuration

Thresholds and the Python executable are configurable under the `Python Maintainability` settings namespace.
