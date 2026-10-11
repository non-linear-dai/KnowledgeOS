import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8');
const palette = Object.fromEntries([...css.matchAll(/--([\w-]+):\s*(#[\da-f]{6});/g)].map(([, key, hex]) => [key, hex]));
function luminance(hex) {
  const rgb = hex.slice(1).match(/../g).map(value => parseInt(value, 16) / 255);
  const linear = rgb.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return linear[0] * .2126 + linear[1] * .7152 + linear[2] * .0722;
}
function contrast(a, b) {
  const values = [luminance(a), luminance(b)].sort((a, b) => b - a);
  return (values[0] + .05) / (values[1] + .05);
}

test('workbench semantic foreground pairs meet WCAG AA normal-text contrast', () => {
  for (const [text, background] of [['foreground', 'background'], ['card-foreground', 'card'], ['muted-foreground', 'muted'], ['muted-foreground', 'card'], ['primary-foreground', 'primary'], ['accent-foreground', 'accent'], ['secondary-foreground', 'secondary']]) {
    assert(contrast(palette[text], palette[background]) >= 4.5, `${text} on ${background} needs 4.5:1`);
  }
});

test('definition category badges retain readable white labels', () => {
  const data = readFileSync(new URL('../app/studio-data.ts', import.meta.url), 'utf8').split('export const kindMeta:')[1];
  for (const [, hex] of data.matchAll(/color: "(#[\da-f]{6})"/g)) assert(contrast('#ffffff', hex) >= 4.5, `${hex} category badge needs 4.5:1`);
});

test('shared styles preserve text sizing, keyboard focus and reduced motion', () => {
  assert(!/font:\s*inherit/.test(css), 'font shorthand must not override component text size');
  assert(css.includes(':focus-visible'));
  assert(css.includes('prefers-reduced-motion: reduce'));
  assert(css.includes('color-scheme: light'), 'keep system dark preference from partially inverting this light theme');
});
