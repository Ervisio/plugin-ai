import type { Chat } from '../../state/chats';
import { describeCall } from '../../chat/summary.ts';

/** A conversation as a Markdown document, for /export. */
export function toMarkdown(c: Chat): string {
  const out = [`# ${c.title || 'Ervisio AI chat'}`, '', `_${new Date(c.created).toISOString()} · ${c.provider} · ${c.model}_`, ''];
  for (const m of c.messages) {
    for (const b of m.content) {
      if (b.type === 'text') out.push(m.role === 'user' ? `**You:** ${b.text}` : b.text, '');
      else if (b.type === 'tool_use') {
        const s = describeCall(b.name, b.input);
        out.push(`> ${s.title} \`${s.detail}\``, '');
      } else if (b.type === 'tool_result') out.push('```', b.content.slice(0, 4000), '```', '');
    }
  }
  return out.join('\n');
}
