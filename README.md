# Python Maintainability

This VS Code extension analyzes only the active Python editor and displays maintainability metrics in the **Maintainability** Activity Bar view. The dashboard and editor decorations use the same metric-specific colors:

- Cyclomatic complexity: red
- Cognitive complexity: purple
- Maximum nesting: blue
- Function length: green
- Parameters: yellow

Click a metric in the dashboard to show only that metric's source decorations. Analysis uses the active editor buffer, including unsaved changes, and runs through the configured Python executable, defaulting to `python3` on macOS.

The Functions section defaults to `Problematic`, showing functions with at least one metric violation. Use the filter buttons to narrow the list to one metric or select `All` to show every function.

Right-click a function and choose **Copy report** to copy a compact report of its threshold violations to the clipboard.

Choose **Show lifecycle** from the same menu to open an on-demand static graph of workspace callers and callees. Resolved nodes navigate to their exact source location; dynamic and external calls remain visibly unresolved instead of being guessed.

The graph marks inferred repository entrypoints from `__main__` guards, conventional `main()` functions, framework or command decorators, and caller-free roots. It follows exact instance types established by constructor assignments, parameter annotations, initialized `self` attributes, and literal `getattr` protocol dispatch. Python project metadata separates sibling repositories inside a larger VS Code workspace.

Lifecycle panels open in **Focused** mode with tests and unresolved calls hidden. **Neighborhood** limits the graph to direct relationships; **Complete** restores the full topology with unresolved calls collapsed into expandable groups.

Click a resolved graph function to make it the new lifecycle focus. The panel keeps a selection history for its **Back** button; clicking the already selected function opens its source.

## Development

```sh
npm install
npm run compile
```

Press `F5` in VS Code to launch an Extension Development Host.
