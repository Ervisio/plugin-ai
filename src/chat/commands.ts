/** What the composer does with a line: a message, a `!shell command`, or a `/command`. */

export interface SlashCommand {
  name: string;
  /** Arguments, for the palette ("on|off"). */
  usage: string;
  /** i18n key of the one-line description */
  help: string;
}

export const COMMANDS: SlashCommand[] = [
  { name: 'new', usage: '', help: 'cmd.new' },
  { name: 'clear', usage: '', help: 'cmd.clear' },
  { name: 'model', usage: '[name]', help: 'cmd.model' },
  { name: 'mode', usage: 'ask|auto-read|auto', help: 'cmd.mode' },
  { name: 'root', usage: 'on|off', help: 'cmd.root' },
  { name: 'tools', usage: '', help: 'cmd.tools' },
  { name: 'export', usage: '', help: 'cmd.export' },
  { name: 'help', usage: '', help: 'cmd.help' },
];

export type Parsed =
  | { kind: 'empty' }
  | { kind: 'shell'; command: string }
  | { kind: 'slash'; name: string; args: string; known: boolean }
  | { kind: 'chat'; text: string };

export function parseInput(raw: string): Parsed {
  const text = raw.trim();
  if (!text) return { kind: 'empty' };
  if (text.startsWith('!') && text.length > 1) return { kind: 'shell', command: text.slice(1).trim() };
  if (text.startsWith('/') && /^\/[a-z-]+(\s|$)/i.test(text)) {
    const [name, ...rest] = text.slice(1).split(/\s+/);
    const n = name.toLowerCase();
    return { kind: 'slash', name: n, args: rest.join(' '), known: COMMANDS.some((c) => c.name === n) };
  }
  return { kind: 'chat', text: raw.replace(/\s+$/, '') };
}

/** Commands whose name starts with what has been typed after the slash (only while no argument has been typed). */
export function suggest(raw: string): SlashCommand[] {
  const m = /^\/([a-z-]*)$/i.exec(raw);
  if (!m) return [];
  return COMMANDS.filter((c) => c.name.startsWith(m[1].toLowerCase()));
}
