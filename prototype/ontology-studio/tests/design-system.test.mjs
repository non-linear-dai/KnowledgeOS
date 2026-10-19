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

test('reference blue and its text-safe variant remain in the same blue family', () => {
  assert.equal(palette['brand-blue'], '#3983f7');
  const rgb = hex => hex.slice(1).match(/../g).map(value => parseInt(value, 16));
  for (const color of [palette['brand-blue'], palette.primary]) {
    const [red, green, blue] = rgb(color);
    assert(blue > green && green > red, 'brand and primary actions should share the reference blue family');
  }
  assert(luminance(palette.primary) < luminance(palette['brand-blue']), 'small white button labels use a darker blue');
});

test('whiteboard types have distinct category colors as well as text labels', () => {
  const data = readFileSync(new URL('../app/studio-data.ts', import.meta.url), 'utf8').split('export const kindMeta:')[1].split('export const initialChangeSets')[0];
  const categories = Object.fromEntries([...data.matchAll(/^\s+(\w+):.*?color: "(#[\da-f]{6})"/gm)].map(([, kind, color]) => [kind, color]));
  const kinds = ['schema', 'concept', 'relation', 'predicate', 'model', 'business_constraint', 'business_rule'];
  const colors = kinds.map(kind => categories[kind]);
  assert.equal(new Set(colors).size, kinds.length, 'every whiteboard type needs its own color');
  const rgb = colors.map(hex => hex.slice(1).match(/../g).map(value => parseInt(value, 16)));
  assert(rgb.filter(values => (Math.max(...values) - Math.min(...values)) / Math.max(...values) > .35).length >= 6, 'classification must not regress to an all-grey palette');
  for (let a = 0; a < rgb.length; a++) for (let b = a + 1; b < rgb.length; b++) {
    assert(Math.hypot(...rgb[a].map((value, index) => value - rgb[b][index])) >= 48, `${kinds[a]} and ${kinds[b]} need visibly distinct colors`);
  }
});

test('shared styles preserve text sizing, keyboard focus and reduced motion', () => {
  assert(!/font:\s*inherit/.test(css), 'font shorthand must not override component text size');
  assert(css.includes(':focus-visible'));
  assert(css.includes('prefers-reduced-motion: reduce'));
  assert(css.includes('color-scheme: light'), 'keep system dark preference from partially inverting this light theme');
});
