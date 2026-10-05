/** One-line, human descriptions of tool calls, for the transcript and the approval card. */

export interface CallSummary {
  /** A verb or area: "Run", "Read", "Edit"... */
  title: string;
  /** What it acts on: the command, the path... */
  detail: string;
  /** Does it change something (as far as the name and arguments say)? Used only for the look of the card. */
  kind: 'read' | 'change' | 'run';
}

const str = (v: unknown): string => (typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v));
const short = (s: string, n = 240): string => (s.length > n ? s.slice(0, n) + '…' : s);

export function describeCall(name: string, input: Record<string, unknown>): CallSummary {
  const root = input.sudo === true ? ' (as root)' : '';
  switch (name) {
    case 'shell_exec':
      return { title: 'Run', detail: `$ ${short(str(input.command))}${root}`, kind: 'run' };
    case 'job_start':
      return { title: 'Start job', detail: `$ ${short(str(input.command))}${root}`, kind: 'run' };
    case 'job_output':
    case 'job_kill':
      return { title: name === 'job_kill' ? 'Stop job' : 'Job output', detail: str(input.id), kind: name === 'job_kill' ? 'change' : 'read' };
    case 'job_list':
      return { title: 'List jobs', detail: '', kind: 'read' };
    case 'terminal_open':
      return { title: 'Open terminal', detail: [str(input.name), str(input.command)].filter(Boolean).join(': '), kind: 'run' };
    case 'terminal_send':
      return { title: 'Type', detail: `${str(input.id)}: ${short(str(input.text ?? (input.keys as unknown[] | undefined)?.join(' ') ?? ''), 160)}${input.enter ? ' ⏎' : ''}`, kind: 'run' };
    case 'terminal_read':
      return { title: 'Read terminal', detail: str(input.id), kind: 'read' };
    case 'terminal_list':
      return { title: 'List terminals', detail: '', kind: 'read' };
    case 'terminal_close':
      return { title: 'Close terminal', detail: str(input.id), kind: 'change' };
    case 'fs_read':
      return { title: 'Read', detail: `${str(input.path)}${root}`, kind: 'read' };
    case 'fs_write':
      return { title: input.append ? 'Append to' : 'Write', detail: `${str(input.path)}${root}${typeof input.content === 'string' ? ` · ${input.content.length} characters` : ''}`, kind: 'change' };
    case 'fs_edit':
      return { title: 'Edit', detail: `${str(input.path)}${root}`, kind: 'change' };
    case 'fs_list':
      return { title: 'List', detail: `${str(input.path ?? '~')}${root}`, kind: 'read' };
    case 'fs_find':
      return { title: 'Find', detail: `${str(input.name ?? input.type ?? '')} in ${str(input.path ?? '/')}`, kind: 'read' };
    case 'fs_search':
      return { title: 'Search', detail: `${short(str(input.pattern), 80)} in ${str(input.path ?? '~')}`, kind: 'read' };
    case 'fs_stat':
    case 'fs_diff':
      return { title: name === 'fs_diff' ? 'Diff' : 'Stat', detail: str(input.path ?? input.a), kind: 'read' };
    case 'fs_manage':
      return { title: str(input.action).replace(/^./, (c) => c.toUpperCase()), detail: `${str(input.path)}${input.dest ? ' → ' + str(input.dest) : ''}${root}`, kind: 'change' };
    case 'fs_archive':
      return { title: `Archive ${str(input.action)}`, detail: str(input.archive), kind: input.action === 'list' ? 'read' : 'change' };
    case 'service_manage':
      return { title: `Service ${str(input.action)}`, detail: `${str(input.unit ?? input.pattern ?? '')}${root}`, kind: ['list', 'status', 'cat'].includes(str(input.action)) ? 'read' : 'change' };
    case 'package_manage':
      return { title: `Package ${str(input.action)}`, detail: `${(input.packages as string[] | undefined)?.join(' ') ?? str(input.query ?? '')}${root}`, kind: ['search', 'info', 'list'].includes(str(input.action)) ? 'read' : 'change' };
    case 'cron_manage':
      return { title: `Cron ${str(input.action)}`, detail: str(input.command ?? input.match ?? ''), kind: input.action === 'list' ? 'read' : 'change' };
    case 'docker_cli':
      return { title: 'Docker', detail: `docker ${short((input.args as string[] | undefined)?.join(' ') ?? '')}${root}`, kind: 'run' };
    case 'net_request':
      return { title: str(input.method ?? 'GET'), detail: str(input.url), kind: input.method && input.method !== 'GET' ? 'change' : 'read' };
    case 'net_check':
      return { title: 'Check', detail: `${str(input.host)}${input.port ? ':' + str(input.port) : ''}`, kind: 'read' };
    case 'ssh_exec':
      return { title: 'SSH', detail: `${str(input.user ? input.user + '@' : '')}${str(input.host)}: ${short(str(input.command), 160)}`, kind: 'run' };
    case 'firewall_manage':
      return { title: `Firewall ${str(input.action)}`, detail: str(input.port ?? ''), kind: input.action === 'status' ? 'read' : 'change' };
    case 'system_info':
    case 'server_context':
      return { title: name === 'system_info' ? 'System info' : 'Server context', detail: '', kind: 'read' };
    case 'system_processes':
      return { title: 'Processes', detail: str(input.filter ?? ''), kind: 'read' };
    case 'process_signal':
      return { title: `Signal ${str(input.signal ?? 'TERM')}`, detail: str(input.pid ?? input.name), kind: 'change' };
    case 'system_network':
      return { title: 'Network', detail: str(input.what ?? 'listening'), kind: 'read' };
    case 'system_logs':
      return { title: 'Logs', detail: str(input.unit ?? input.file ?? 'journal'), kind: 'read' };
    case 'ervisio_call':
      return { title: 'Ervisio', detail: str(input.method), kind: 'run' };
    case 'ervisio_status':
    case 'ervisio_methods':
    case 'ervisio_audit':
      return { title: name.replace('ervisio_', 'Ervisio '), detail: '', kind: 'read' };
    case 'ervisio_plugin_exec':
      return { title: 'Plugin command', detail: `${str(input.plugin)} ${str(input.command)}`, kind: 'run' };
    case 'memory_read':
      return { title: 'Read memory', detail: '', kind: 'read' };
    case 'memory_write':
      return { title: 'Write memory', detail: short(str(input.text), 100), kind: 'change' };
    default:
      return { title: name, detail: short(JSON.stringify(input), 200), kind: 'run' };
  }
}
