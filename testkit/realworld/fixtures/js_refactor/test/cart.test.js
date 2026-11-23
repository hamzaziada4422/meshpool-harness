const test = require('node:test');
const assert = require('node:assert');
const { lineTotal, cartTotal, summary } = require('../lib/cart');

test('line total with tax', () => {
  assert.strictEqual(lineTotal({ price: 1000, qty: 2, region: 'EU' }), 2400);
  assert.strictEqual(lineTotal({ price: 999, qty: 1, region: 'US' }), 1069);
  assert.strictEqual(lineTotal({ price: 500, qty: 3, region: 'JP' }), 1500);
});

test('cart total adds shipping under the threshold', () => {
  assert.strictEqual(cartTotal({ region: 'US', lines: [{ price: 1000, qty: 1 }] }), 1070 + 495);
  assert.strictEqual(cartTotal({ region: 'EU', lines: [{ price: 2500, qty: 2 }] }), 6000);
  assert.strictEqual(cartTotal({ region: 'EU', lines: [] }), 0);
});

test('summary', () => {
  assert.strictEqual(summary({ region: 'UK', lines: [{ price: 1000, qty: 2 }, { price: 250, qty: 4 }] }),
    '6 items, 40.95');
});
