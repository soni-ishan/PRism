/**
 * PRism VS Code Extension — Entry Point
 * ======================================
 * Registers the sidebar webview, commands, and auto-refresh polling.
 *
 * Adapted from SnipSage extension patterns (same author).
 */

import * as vscode from "vscode";

import { SidebarProvider } from "./sidebarProvider";
import * as crypto from "crypto";

let pollTimer: ReturnType<typeof setInterval> | undefined;

export function activate(context: vscode.ExtensionContext) {
  // 1. Get or generate a unique ID for this user's machine
  let clientId = context.globalState.get<string>('prism.clientId');
  if (!clientId) {
    clientId = crypto.randomUUID();
    context.globalState.update('prism.clientId', clientId);
  }

  // 2. Pass it to your SidebarProvider
  const sidebarProvider = new SidebarProvider(context.extensionUri, clientId!);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(
      SidebarProvider.viewType,
      sidebarProvider,
      { webviewOptions: { retainContextWhenHidden: true } }
    )
  );

  // ── Commands ───────────────────────────────────────────────────

  // Re-run analysis (refresh button in sidebar title bar)
  context.subscriptions.push(
    vscode.commands.registerCommand("prism.rerunAnalysis", () => {
      sidebarProvider.refresh();
    })
  );

  // Show full report in an editor panel
  context.subscriptions.push(
    vscode.commands.registerCommand("prism.showReport", () => {
      sidebarProvider.showFullReport();
    })
  );

  // ── Auto-refresh polling ───────────────────────────────────────
  const config = vscode.workspace.getConfiguration("prism");
  if (config.get<boolean>("autoRefresh", true)) {
    const intervalSec = config.get<number>("refreshIntervalSeconds", 30);
    pollTimer = setInterval(() => {
      sidebarProvider.refresh();
    }, intervalSec * 1000);
  }

  // Re-read config on change
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("prism")) {
        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = undefined;
        }
        const cfg = vscode.workspace.getConfiguration("prism");
        if (cfg.get<boolean>("autoRefresh", true)) {
          const sec = cfg.get<number>("refreshIntervalSeconds", 30);
          pollTimer = setInterval(() => sidebarProvider.refresh(), sec * 1000);
        }
      }
    })
  );
}

export function deactivate() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = undefined;
  }
}
