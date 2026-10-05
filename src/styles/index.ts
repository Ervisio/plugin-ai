/**
 * All CSS of the plugin as one <style>, injected once. Every file in this folder ending in .css is included
 * (alphabetical order). Use theme variables only; prefix class names with `ai-`.
 */
const files = import.meta.glob<string>('./*.css', { query: '?inline', import: 'default', eager: true });

export function injectStyles(): void {
  if (document.getElementById('ai-styles')) return;
  const el = document.createElement('style');
  el.id = 'ai-styles';
  el.textContent = Object.keys(files).sort().map((k) => files[k]).join('\n');
  document.head.appendChild(el);
}
