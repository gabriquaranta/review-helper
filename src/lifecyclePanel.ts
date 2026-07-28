import * as vscode from "vscode";
import { LifecycleResult } from "./types";

type LifecyclePanelMessage =
  | { readonly command: "openNode"; readonly nodeId: string }
  | { readonly command: "selectNode"; readonly nodeId: string }
  | { readonly command: "goBack" };

interface LifecycleSelection {
  readonly symbolId: string;
  readonly label: string;
}

type LifecycleLoader = (symbolId: string) => Promise<LifecycleResult>;

export class LifecyclePanel {
  private readonly panel: vscode.WebviewPanel;
  private readonly extensionUri: vscode.Uri;
  private readonly loadLifecycle: LifecycleLoader;
  private navigableNodes = new Map<string, { readonly uri: string; readonly line: number; readonly column: number; readonly label: string }>();
  private readonly history: LifecycleSelection[] = [];
  private currentSelection: LifecycleSelection | undefined;
  private requestVersion = 0;

  /**
   * Create and initialize a symbol lifecycle panel.
   *
   * Rendering the loading state immediately makes the on-demand workspace scan explicit.
   */
  public static create(
    extensionUri: vscode.Uri,
    initialSelection: LifecycleSelection,
    loadLifecycle: LifecycleLoader,
  ): LifecyclePanel {
    const panel = vscode.window.createWebviewPanel(
      "pythonMaintainability.lifecycle",
      `Lifecycle: ${initialSelection.label}`,
      vscode.ViewColumn.Beside,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [
          vscode.Uri.joinPath(extensionUri, "resources"),
        ],
      },
    );
    const lifecyclePanel = new LifecyclePanel(panel, extensionUri, loadLifecycle);
    void lifecyclePanel.navigate(initialSelection, false);
    return lifecyclePanel;
  }

  /**
   * Configure panel messaging and its initial state.
   *
   * Keeping navigation in the extension host prevents the webview from opening arbitrary resources.
   */
  private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri, loadLifecycle: LifecycleLoader) {
    this.panel = panel;
    this.extensionUri = extensionUri;
    this.loadLifecycle = loadLifecycle;
    this.panel.webview.onDidReceiveMessage((message: LifecyclePanelMessage) => {
      if (message.command === "openNode") void this.openNode(message.nodeId);
      if (message.command === "selectNode") void this.selectNode(message.nodeId);
      if (message.command === "goBack") void this.goBack();
    });
  }

  /**
   * Load and render one symbol lifecycle.
   *
   * Versioning prevents a slower previous request from replacing the latest selection.
   */
  private async navigate(selection: LifecycleSelection, rememberCurrent: boolean): Promise<void> {
    if (rememberCurrent && this.currentSelection) this.history.push(this.currentSelection);
    this.currentSelection = selection;
    this.panel.title = `Lifecycle: ${selection.label}`;
    this.panel.webview.html = loadingContent();
    const currentVersion = ++this.requestVersion;
    let result: LifecycleResult;
    try {
      result = await this.loadLifecycle(selection.symbolId);
    } catch (error) {
      result = failureResult(selection.symbolId, error instanceof Error ? error.message : "Lifecycle analysis failed.");
    }
    if (currentVersion !== this.requestVersion) return;
    this.navigableNodes = new Map(result.nodes.flatMap((node) =>
      node.uri !== null && node.line !== null && node.column !== null
        ? [[node.id, { uri: node.uri, line: node.line, column: node.column, label: node.label }]]
        : [],
    ));
    this.panel.webview.html = graphContent(this.panel.webview, this.extensionUri, result, this.history.length > 0);
  }

  /**
   * Shift the panel to a resolved graph symbol.
   *
   * Looking up the selection host-side prevents arbitrary IDs or labels from entering navigation history.
   */
  private async selectNode(nodeId: string): Promise<void> {
    if (nodeId === this.currentSelection?.symbolId) {
      await this.openNode(nodeId);
      return;
    }
    const target = this.navigableNodes.get(nodeId);
    if (!target) return;
    await this.navigate({ symbolId: nodeId, label: target.label }, true);
  }

  /**
   * Restore the previous graph selection.
   *
   * Popping history before navigation gives repeated Back actions standard stack behavior.
   */
  private async goBack(): Promise<void> {
    const previous = this.history.pop();
    if (previous) await this.navigate(previous, false);
  }

  /**
   * Open the exact source position represented by a resolved graph node.
   *
   * Validating numeric positions and using a file URI confines navigation to analyzer-produced locations.
   */
  private async openNode(nodeId: string): Promise<void> {
    const target = this.navigableNodes.get(nodeId);
    if (!target) return;
    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(target.uri));
    const editor = await vscode.window.showTextDocument(document, { preview: true });
    const position = new vscode.Position(target.line, target.column);
    editor.selection = new vscode.Selection(position, position);
    editor.revealRange(new vscode.Range(position, position), vscode.TextEditorRevealType.InCenterIfOutsideViewport);
  }
}

/**
 * Render the panel's initial analyzing state.
 *
 * A standalone state remains valid even if analysis takes noticeable time.
 */
function loadingContent(): string {
  return `<!doctype html><html><head><meta charset="UTF-8"><style>${baseStyles()}</style></head>
    <body class="state"><div class="spinner"></div><h1>Analyzing symbol lifecycle…</h1>
    <p>Indexing Python files in the current workspace.</p></body></html>`;
}

/**
 * Create a lifecycle result for an unexpected loader failure.
 *
 * Keeping failures inside the normal result contract preserves a stable panel error state.
 */
function failureResult(selectedSymbolId: string, error: string): LifecycleResult {
  return {
    selectedSymbolId,
    nodes: [],
    edges: [],
    truncated: false,
    warnings: [],
    error,
  };
}

/**
 * Render a lifecycle result and its interactive SVG graph.
 *
 * Local scripts, a strict content policy, and DOM text nodes keep workspace symbols out of executable HTML.
 */
function graphContent(
  webview: vscode.Webview,
  extensionUri: vscode.Uri,
  result: LifecycleResult,
  canGoBack: boolean,
): string {
  if (result.error) {
    return `<!doctype html><html><head><meta charset="UTF-8"><style>${baseStyles()}</style></head>
      <body class="state error"><h1>Lifecycle unavailable</h1><p>${escapeHtml(result.error)}</p></body></html>`;
  }
  if (result.nodes.length === 0) {
    return `<!doctype html><html><head><meta charset="UTF-8"><style>${baseStyles()}</style></head>
      <body class="state"><h1>No lifecycle found</h1><p>No statically resolvable symbols are connected to this symbol.</p></body></html>`;
  }
  const nonce = createNonce();
  const dagreUri = webview.asWebviewUri(
    vscode.Uri.joinPath(extensionUri, "resources", "dagre.min.js"),
  );
  const serializedResult = JSON.stringify(result).replaceAll("<", "\\u003c");
  const warning = result.warnings.length > 0
    ? `<details><summary>${result.warnings.length} file${result.warnings.length === 1 ? "" : "s"} skipped</summary><ul>${result.warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></details>`
    : "";
  const truncated = result.truncated
    ? '<div class="notice">Graph limited to 100 nodes. Narrow the workspace or remove unresolved calls to see more.</div>'
    : "";
  return `<!doctype html><html><head><meta charset="UTF-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource}; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
    <style nonce="${nonce}">${baseStyles()}${graphStyles()}</style></head><body>
    <header><div class="heading"><button id="back" type="button" class="back" ${canGoBack ? "" : "disabled"} aria-label="Go to previous symbol">Back</button><div><h1>Code lifecycle</h1><p>Static calls, construction, ownership, and inheritance</p></div></div>
      <div class="controls">
        <label>View <select id="view-mode"><option value="focused">Focused</option><option value="neighborhood">Neighborhood</option><option value="complete">Complete</option></select></label>
        <label><input id="callers" type="checkbox" checked> Callers</label>
        <label><input id="callees" type="checkbox" checked> Callees</label>
        <label><input id="include-tests" type="checkbox"> Tests</label>
        <button id="center" type="button" class="secondary">Center selected</button>
        <button id="fit" type="button">Fit graph</button>
      </div>
    </header>${truncated}${warning}
    <div class="legend"><span class="selected-key">Selected</span><span class="entrypoint-key">Entrypoint</span><span class="caller-key">Caller</span>
      <span class="callee-key">Callee</span><span class="both-key">Both</span><span class="class-key">Class</span><span class="unresolved-key">Unresolved</span>
      <span class="relationship-key">Edges: call · owns · creates · inherits</span></div>
    <main id="viewport"><svg id="graph" role="img" aria-label="Code lifecycle graph">
      <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 z"></path></marker></defs><g id="canvas"></g></svg></main>
    <script nonce="${nonce}" src="${dagreUri}"></script>
    <script nonce="${nonce}">
      const vscode = acquireVsCodeApi();
      const result = ${serializedResult};
      const svg = document.getElementById('graph');
      const canvas = document.getElementById('canvas');
      const viewport = document.getElementById('viewport');
      const viewMode = document.getElementById('view-mode');
      const callers = document.getElementById('callers');
      const callees = document.getElementById('callees');
      const includeTests = document.getElementById('include-tests');
      const expandedUnresolved = new Set();
      let view = {x:0,y:0,w:1000,h:700};
      let currentLayout;
      let currentDimensions;
      let dragging = false;
      let pointer = {x:0,y:0};

      function directionVisible(node) {
        if (node.role === 'selected') return true;
        if (node.role === 'entrypoint' || node.role === 'caller') return callers.checked;
        if (node.role === 'callee' || node.role === 'unresolved') return callees.checked;
        return callers.checked || callees.checked;
      }
      function testVisible(node) {
        return includeTests.checked || !node.isTest || node.id === result.selectedSymbolId;
      }
      function element(name, attributes = {}) {
        const node = document.createElementNS('http://www.w3.org/2000/svg', name);
        for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
        return node;
      }
      function displayLabel(node) {
        const label = node.label.replaceAll('.<locals>.',' › ');
        return node.kind === 'class' ? 'Class · '+label : label;
      }
      function clipMiddle(value, maximum) {
        if (value.length <= maximum) return value;
        const left = Math.ceil((maximum-1)*0.42);
        const right = maximum-1-left;
        return value.slice(0,left)+'…'+value.slice(-right);
      }
      function nodeWidth(node) {
        const longest = Math.max(displayLabel(node).length,node.detail.length);
        return Math.max(210,Math.min(360,longest*7+24));
      }
      function graphData() {
        const resolved = result.nodes.filter((node) => node.role !== 'unresolved' && directionVisible(node) && testVisible(node));
        let resolvedIds = new Set(resolved.map((node) => node.id));
        if (viewMode.value === 'neighborhood') {
          const neighborhoodIds = new Set([result.selectedSymbolId]);
          for (const edge of result.edges) {
            if (edge.source === result.selectedSymbolId) neighborhoodIds.add(edge.target);
            if (edge.target === result.selectedSymbolId) neighborhoodIds.add(edge.source);
          }
          resolvedIds = new Set([...resolvedIds].filter((id) => neighborhoodIds.has(id)));
        }
        const nodes = resolved.filter((node) => resolvedIds.has(node.id));
        const edges = result.edges.filter((edge) => resolvedIds.has(edge.source) && resolvedIds.has(edge.target));
        if (viewMode.value !== 'complete' || !callees.checked) return {nodes,edges};
        const unresolvedByOwner = new Map();
        for (const edge of result.edges) {
          const target = result.nodes.find((node) => node.id === edge.target);
          if (!resolvedIds.has(edge.source) || !target || target.role !== 'unresolved' || !testVisible(target)) continue;
          const group = unresolvedByOwner.get(edge.source) || [];
          group.push(target);
          unresolvedByOwner.set(edge.source,group);
        }
        for (const [owner, unresolved] of unresolvedByOwner) {
          if (expandedUnresolved.has(owner)) {
            nodes.push(...unresolved);
            edges.push(...unresolved.map((node) => ({source:owner,target:node.id})));
          } else {
            const ownerNode = nodes.find((node) => node.id === owner);
            const aggregateId = 'unresolved-group:'+owner;
            nodes.push({
              id:aggregateId,
              label:'+'+unresolved.length+' unresolved call'+(unresolved.length===1?'':'s'),
              detail:'Click to expand',
              uri:null,
              line:null,
              column:null,
              role:'unresolved',
              entrypointReason:null,
              isTest:ownerNode?.isTest || false,
              kind:'unresolved',
              aggregateOwner:owner,
            });
            edges.push({source:owner,target:aggregateId,kind:'calls'});
          }
        }
        return {nodes,edges};
      }
      function render(resetView = true) {
        canvas.replaceChildren();
        const data = graphData();
        const nodes = data.nodes;
        const edges = data.edges;
        const layout = new dagre.graphlib.Graph().setGraph({rankdir:'LR',ranksep:90,nodesep:30,marginx:35,marginy:35}).setDefaultEdgeLabel(()=>({}));
        for (const node of nodes) layout.setNode(node.id,{width:nodeWidth(node),height:62});
        for (const edge of edges) layout.setEdge(edge.source,edge.target);
        dagre.layout(layout);
        for (const edge of edges) {
          const positioned = layout.edge(edge.source,edge.target);
          if (!positioned) continue;
          const path = element('path',{class:'edge '+edge.kind,d:'M'+positioned.points.map((point) => point.x+','+point.y).join(' L'),'marker-end':'url(#arrow)','data-source':edge.source,'data-target':edge.target});
          canvas.append(path);
        }
        for (const node of nodes) {
          const position = layout.node(node.id);
          const group = element('g',{class:'node '+node.role+' '+node.kind,transform:'translate('+(position.x-position.width/2)+','+(position.y-position.height/2)+')',tabindex:'0',role:'button','data-node-id':node.id});
          group.append(element('rect',{width:position.width,height:position.height,rx:7}));
          const label = element('text',{x:12,y:23,class:'node-label'});
          const characterLimit = Math.floor((position.width-24)/7);
          label.textContent = clipMiddle(displayLabel(node),characterLimit);
          const detail = element('text',{x:12,y:43,class:'node-detail'});
          detail.textContent = clipMiddle(node.detail,characterLimit);
          const title = element('title');
          title.textContent = node.label+' — '+node.detail;
          group.append(title,label,detail);
          if (node.aggregateOwner) {
            const expand = () => {expandedUnresolved.add(node.aggregateOwner);render();};
            group.addEventListener('click',expand);
            group.addEventListener('keydown',(event)=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();expand();}});
          } else if (node.uri !== null && node.line !== null && node.column !== null) {
            const select = () => vscode.postMessage({command:node.id===result.selectedSymbolId?'openNode':'selectNode',nodeId:node.id});
            group.addEventListener('click',select);
            group.addEventListener('keydown',(event)=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select();}});
          } else {
            group.removeAttribute('tabindex');
            group.removeAttribute('role');
          }
          group.addEventListener('pointerdown',(event)=>event.stopPropagation());
          group.addEventListener('pointerenter',()=>highlight(node.id,edges));
          group.addEventListener('pointerleave',clearHighlight);
          canvas.append(group);
        }
        const dimensions = layout.graph();
        currentLayout = layout;
        currentDimensions = dimensions;
        if (resetView) fit();
      }
      function applyView(){svg.setAttribute('viewBox',view.x+' '+view.y+' '+view.w+' '+view.h);}
      function fit(){
        if (!currentDimensions) return;
        view={x:0,y:0,w:Math.max(currentDimensions.width,400),h:Math.max(currentDimensions.height,300)};
        applyView();
      }
      function centerSelected(){
        const selected=currentLayout?.node(result.selectedSymbolId);
        if(!selected)return;
        const aspect=Math.max(viewport.clientWidth/Math.max(viewport.clientHeight,1),1);
        const width=900;
        view={x:selected.x-width/2,y:selected.y-width/aspect/2,w:width,h:width/aspect};
        applyView();
      }
      function highlight(nodeId,edges){
        const related=new Set([nodeId]);
        for(const edge of edges){
          if(edge.source===nodeId)related.add(edge.target);
          if(edge.target===nodeId)related.add(edge.source);
        }
        canvas.querySelectorAll('.node').forEach((node)=>node.classList.toggle('dimmed',!related.has(node.dataset.nodeId)));
        canvas.querySelectorAll('.edge').forEach((edge)=>edge.classList.toggle('dimmed',edge.dataset.source!==nodeId&&edge.dataset.target!==nodeId));
      }
      function clearHighlight(){canvas.querySelectorAll('.dimmed').forEach((node)=>node.classList.remove('dimmed'));}
      viewMode.addEventListener('change',()=>{expandedUnresolved.clear();render();});
      callers.addEventListener('change',()=>render());
      callees.addEventListener('change',()=>render());
      includeTests.addEventListener('change',()=>render());
      document.getElementById('center').addEventListener('click',centerSelected);
      document.getElementById('fit').addEventListener('click',fit);
      document.getElementById('back').addEventListener('click',()=>vscode.postMessage({command:'goBack'}));
      svg.addEventListener('wheel',(event)=>{
        event.preventDefault();
        const scale=event.deltaY<0?0.88:1.14;
        const rect=svg.getBoundingClientRect();
        const x=view.x+(event.clientX-rect.left)/rect.width*view.w;
        const y=view.y+(event.clientY-rect.top)/rect.height*view.h;
        view={x:x-(x-view.x)*scale,y:y-(y-view.y)*scale,w:view.w*scale,h:view.h*scale};applyView();
      },{passive:false});
      svg.addEventListener('pointerdown',(event)=>{dragging=true;pointer={x:event.clientX,y:event.clientY};svg.setPointerCapture(event.pointerId);});
      svg.addEventListener('pointermove',(event)=>{if(!dragging)return;const rect=svg.getBoundingClientRect();view.x-=(event.clientX-pointer.x)/rect.width*view.w;view.y-=(event.clientY-pointer.y)/rect.height*view.h;pointer={x:event.clientX,y:event.clientY};applyView();});
      svg.addEventListener('pointerup',()=>{dragging=false;});
      render();
      if(currentDimensions.width>1600||currentDimensions.height>1000)centerSelected();
    </script></body></html>`;
}

/**
 * Return shared panel styling.
 *
 * Reusing VS Code theme variables keeps all lifecycle states native to the host.
 */
function baseStyles(): string {
  return `*{box-sizing:border-box}body{margin:0;padding:18px;font-family:var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-editor-background);font-size:13px}h1{font-size:18px;margin:0 0 4px}p{margin:0;color:var(--vscode-descriptionForeground)}.state{min-height:100vh;display:grid;place-content:center;text-align:center;gap:10px}.error p{color:var(--vscode-errorForeground)}.spinner{width:24px;height:24px;margin:auto;border:2px solid var(--vscode-panel-border);border-top-color:var(--vscode-progressBar-background);border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}`;
}

/**
 * Return graph-specific styling.
 *
 * Role colors and interaction states make direction and navigability legible without custom assets.
 */
function graphStyles(): string {
  return `header{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:12px}.heading{display:flex;align-items:center;gap:10px}.back{font:inherit;color:var(--vscode-button-secondaryForeground);background:var(--vscode-button-secondaryBackground);border:0;padding:5px 9px;border-radius:3px;cursor:pointer}.back:hover:not(:disabled){background:var(--vscode-button-secondaryHoverBackground)}.back:disabled{opacity:.45;cursor:default}.controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.controls label{white-space:nowrap}.controls select{margin-left:4px;font:inherit;color:var(--vscode-dropdown-foreground);background:var(--vscode-dropdown-background);border:1px solid var(--vscode-dropdown-border);padding:4px 22px 4px 7px;border-radius:3px}.controls button{font:inherit;color:var(--vscode-button-foreground);background:var(--vscode-button-background);border:0;padding:5px 10px;border-radius:3px;cursor:pointer}.controls button.secondary{color:var(--vscode-button-secondaryForeground);background:var(--vscode-button-secondaryBackground)}.controls button:hover{background:var(--vscode-button-hoverBackground)}.controls button.secondary:hover{background:var(--vscode-button-secondaryHoverBackground)}.notice{padding:8px 10px;margin-bottom:8px;border-left:3px solid var(--vscode-editorWarning-foreground);background:var(--vscode-textBlockQuote-background)}details{margin:8px 0;color:var(--vscode-descriptionForeground)}details ul{max-height:110px;overflow:auto}.legend{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0;font-size:11px;color:var(--vscode-descriptionForeground)}.legend span::before{content:'';display:inline-block;width:9px;height:9px;margin-right:5px;border-radius:2px;background:var(--key)}.selected-key{--key:var(--vscode-focusBorder)}.entrypoint-key{--key:#e05252}.caller-key{--key:#a855f7}.callee-key{--key:#22a06b}.both-key{--key:#d29922}.class-key{--key:#3b82f6}.unresolved-key{--key:var(--vscode-disabledForeground)}.relationship-key::before{display:none!important}main{height:calc(100vh - 125px);min-height:320px;border:1px solid var(--vscode-panel-border);border-radius:6px;overflow:hidden;background:var(--vscode-sideBar-background)}svg{width:100%;height:100%;cursor:grab;touch-action:none}svg:active{cursor:grabbing}.edge{fill:none;stroke:var(--vscode-descriptionForeground);stroke-width:1.4;opacity:.65;transition:opacity .12s}.edge.contains{stroke-dasharray:3 3}.edge.constructs{stroke:#3b82f6;stroke-width:1.8}.edge.inherits{stroke:#d29922;stroke-width:1.8}marker path{fill:var(--vscode-descriptionForeground)}.node{cursor:pointer;transition:opacity .12s}.node.dimmed,.edge.dimmed{opacity:.12}.node rect{fill:var(--vscode-editorWidget-background);stroke:var(--vscode-panel-border);stroke-width:2}.node.class rect{stroke-width:3;stroke-dasharray:7 2}.node:hover rect,.node:focus rect{stroke:var(--vscode-focusBorder)}.node:focus{outline:none}.node.selected rect{stroke:var(--vscode-focusBorder);fill:color-mix(in srgb,var(--vscode-focusBorder) 14%,var(--vscode-editorWidget-background))}.node.entrypoint rect{stroke:#e05252;stroke-width:3}.node.caller rect{stroke:#a855f7}.node.callee rect{stroke:#22a06b}.node.both rect{stroke:#d29922}.node.unresolved rect{stroke:var(--vscode-disabledForeground);stroke-dasharray:5 4;opacity:.8}.node-label{fill:var(--vscode-foreground);font-size:12px;font-weight:600}.node-detail{fill:var(--vscode-descriptionForeground);font-size:10px}`;
}

/**
 * Escape text interpolated into non-script HTML states.
 *
 * Entity encoding prevents analyzer errors and warnings from creating markup.
 */
function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[character] ?? character);
}

/**
 * Create a content-security-policy nonce.
 *
 * A per-render random value permits only the panel's intended local scripts and styles.
 */
function createNonce(): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  return Array.from({ length: 32 }, () => alphabet[Math.floor(Math.random() * alphabet.length)]).join("");
}
