/** The plugin's own router: one page, and this decides which view it shows. */
import { createStore } from './state/store.ts';

export type View = 'welcome' | 'chat' | 'server' | 'connect' | 'activity' | 'settings';

export const route = createStore<View>('welcome');
export const go = (v: View): void => route.set(v);
