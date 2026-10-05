import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseInput, suggest } from '../src/chat/commands.ts';

test('plain text is a message; trailing whitespace goes, inner newlines stay', () => {
  assert.deepEqual(parseInput('hello\nworld  \n'), { kind: 'chat', text: 'hello\nworld' });
  assert.deepEqual(parseInput('   '), { kind: 'empty' });
});

test('! runs a shell command, a lone ! is just text', () => {
  assert.deepEqual(parseInput('!ls -la /etc'), { kind: 'shell', command: 'ls -la /etc' });
  assert.deepEqual(parseInput('!'), { kind: 'chat', text: '!' });
});

test('/ commands with arguments, known and unknown', () => {
  assert.deepEqual(parseInput('/mode auto-read'), { kind: 'slash', name: 'mode', args: 'auto-read', known: true });
  assert.deepEqual(parseInput('/NEW'), { kind: 'slash', name: 'new', args: '', known: true });
  assert.deepEqual(parseInput('/frobnicate x'), { kind: 'slash', name: 'frobnicate', args: 'x', known: false });
});

test('a path is not a command', () => {
  assert.deepEqual(parseInput('/etc/nginx/nginx.conf is broken'), { kind: 'chat', text: '/etc/nginx/nginx.conf is broken' });
  assert.deepEqual(parseInput('/var'), { kind: 'slash', name: 'var', args: '', known: false });
});

test('suggestions follow the prefix and stop once an argument starts', () => {
  assert.deepEqual(suggest('/').map((c) => c.name).slice(0, 3), ['new', 'clear', 'model']);
  assert.deepEqual(suggest('/mo').map((c) => c.name), ['model', 'mode']);
  assert.deepEqual(suggest('/mode '), []);
  assert.deepEqual(suggest('hello'), []);
});
