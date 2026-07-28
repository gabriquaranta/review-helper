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

## Development

```sh
npm install
npm run compile
```

Press `F5` in VS Code to launch an Extension Development Host.
