/**
 * PRism VS Code Extension — Entry Point
 * ======================================
 * Registers the sidebar webview and commands.
 * Analysis runs once on startup and again after every git commit.
 *
 * Adapted from SnipSage extension patterns (same author).
 */

import * as vscode from "vscode";

import { SidebarProvider } from "./sidebarProvider";
import * as crypto from "crypto";

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

  // ── Commit-triggered refresh ───────────────────────────────────
  // Re-analyze automatically after every git commit; no time-based polling.
  const gitExtension = vscode.extensions.getExtension<any>('vscode.git');
  if (gitExtension) {
    const registerRepoWatcher = (gitApi: any) => {
      const watchRepo = (repo: any) => {
        context.subscriptions.push(
          repo.onDidCommit(() => sidebarProvider.refresh())
        );
      };
      (gitApi.repositories ?? []).forEach(watchRepo);
      context.subscriptions.push(gitApi.onDidOpenRepository(watchRepo));
    };

    if (gitExtension.isActive) {
      registerRepoWatcher(gitExtension.exports.getAPI(1));
    } else {
      gitExtension.activate().then(() =>
        registerRepoWatcher(gitExtension.exports.getAPI(1))
      );
    }
  }
}

export function deactivate() {
  // nothing to clean up
}
