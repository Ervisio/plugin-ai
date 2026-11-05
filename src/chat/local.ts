/** Optional host integration, available in ErvisiOS and the classic Ervisio shell. */
import { getSdk, type PluginSDK } from '../sdk';

export interface LocalResult { handled: boolean; message?: string }
type ShellSdk = PluginSDK & { shell?: { mode: 'classic' | 'ervisios'; command(text: string): Promise<LocalResult> } };

export function hasLocalCommands(): boolean {
  return !!(getSdk() as ShellSdk).shell?.command;
}

export async function localCommand(text: string): Promise<LocalResult> {
  const shell = (getSdk() as ShellSdk).shell;
  return shell ? shell.command(text) : { handled: false };
}
