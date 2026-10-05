import { getSdk } from '../sdk';

/** Icons the app kit does not have. Use them by name (`<Icon name="ai-sparkle" />`) once activate() has run. */
export function registerAiIcons(): void {
  const reg = getSdk().ui.registerIcon as ((name: string, svg: string) => void) | undefined;
  if (!reg) return;
  reg('ai-sparkle', '<path d="M11 3l1.9 5.6L18.5 10.5l-5.6 1.9L11 18l-1.9-5.6L3.5 10.5l5.6-1.9z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/>');
  reg('ai-send', '<path d="M12 19V5M5.5 11.5L12 5l6.5 6.5"/>');
  reg('ai-tool', '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3.5 17.5a1.4 1.4 0 0 0 2 2l5.8-5.8a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.1-.5-.5-2.1z"/>');
  reg('ai-plug', '<path d="M9 3v5M15 3v5M6.5 8h11v3a5.5 5.5 0 0 1-11 0z"/><path d="M12 16.5V21"/>');
  reg('ai-chat', '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4.5 4v-4H5.5A1.5 1.5 0 0 1 4 14.5z"/>');
  reg('ai-bot', '<rect x="4" y="8" width="16" height="11" rx="3.5"/><path d="M12 8V5M9.5 13v1M14.5 13v1M9.5 17h5"/><circle cx="12" cy="4" r="1"/>');
  reg('ai-prompt', '<path d="M5 8l5 4-5 4M12.5 17H19"/>');
}

export const NAV_ICONS = {
  chat: 'ai-chat',
  server: 'server',
  connect: 'ai-plug',
  activity: 'clock',
  settings: 'cog',
} as const;
