/**
 * The plugin SDK (contract version 3) as this plugin uses it: the types and the module-level holder come from
 * @ervisio/plugin-sdk. The SDK exists only after activate() ran: call getSdk() inside functions and components, never
 * at import time.
 */
export type {
  DownloadOptions,
  DownloadResult,
  DownloadStarted,
  ExecResult,
  AuditEntry,
  FileEntry,
  HttpRequest,
  HttpResponse,
  HttpStreamHandlers,
  PluginEnv,
  JobInstance,
  JobRun,
  JobSchedule,
  PluginError,
  PluginSDK,
  PtyOptions,
  Query,
  Theme,
  UploadHandle,
  UploadOptions,
} from '@ervisio/plugin-sdk';
export { getSdk, setSdk } from '@ervisio/plugin-sdk';
