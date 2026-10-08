const test = require('node:test');
const assert = require('node:assert/strict');
const { blockedInput, supportedHost, validatePayload } = require('../security.cjs');
test('requested hotkeys blocked, accessible Tab retained', () => {
  for (const input of [{ key: 'c', control: true }, { key: 'v', control: true }, { key: 'Tab', alt: true }, { key: 'Meta', meta: true }, { key: 'PrintScreen' }]) assert.equal(blockedInput(input), true);
  assert.equal(blockedInput({ key: 'Tab' }), false);
  assert.equal(blockedInput({ key: 'a' }), false);
});
test('Wayland never claims complete protection', () => {
  assert.equal(supportedHost('linux', { DISPLAY: ':0', WAYLAND_DISPLAY: 'wayland-1' }).supported, false);
  assert.equal(supportedHost('linux', { DISPLAY: ':1' }).supported, true);
  assert.equal(supportedHost('win32', {}).supported, true);
});
test('IPC accepts only known routes and constrained camera/answers', () => {
  assert.throws(() => validatePayload('http://evil.test', {}));
  assert.throws(() => validatePayload('start', { camera_index: -1 }));
  assert.throws(() => validatePayload('start', { camera_index: 9 }));
  assert.throws(() => validatePayload('start', { camera_index: 0, video: '/private/file' }));
  assert.throws(() => validatePayload('answers', { answers: { q1: '<script>' } }));
  assert.doesNotThrow(() => validatePayload('start', { camera_index: 0 }));
  assert.doesNotThrow(() => validatePayload('answers', { answers: { q1: 'A', q2: 'D' } }));
});
