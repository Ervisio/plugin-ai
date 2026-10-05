import { createElement, memo, type FunctionComponent } from 'react';

/**
 * `memo`, created on first render. The SDK's React exists only after activate() ran, and the SDK forbids calling
 * `memo` at import time, so a plain `const X = memo(...)` at module level would throw while the plugin loads.
 */
export function lazyMemo<P extends object>(fn: FunctionComponent<P>): FunctionComponent<P> {
  let inner: unknown;
  const Lazy: FunctionComponent<P> = (props) => createElement((inner ??= memo(fn)) as FunctionComponent<P>, props);
  Lazy.displayName = fn.name || 'Memo';
  return Lazy;
}
