/** A tiny external store: `get`, `set`, `subscribe`, and a hook for components. */
import { useSyncExternalStore } from 'react';

export interface Store<T> {
  get(): T;
  set(next: T | ((s: T) => T)): void;
  subscribe(fn: () => void): () => void;
  use(): T;
}

export function createStore<T>(initial: T): Store<T> {
  let state = initial;
  const subs = new Set<() => void>();
  const subscribe = (fn: () => void) => {
    subs.add(fn);
    return () => subs.delete(fn);
  };
  const get = () => state;
  return {
    get,
    subscribe,
    set(next) {
      const v = typeof next === 'function' ? (next as (s: T) => T)(state) : next;
      if (Object.is(v, state)) return;
      state = v;
      subs.forEach((f) => f());
    },
    use: () => useSyncExternalStore(subscribe, get),
  };
}
