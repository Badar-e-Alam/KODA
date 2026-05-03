// Tests for cart.js — uses node:test (built into Node 18+, no npm install).
//
// Run with:  node --test cart.test.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { subtotal, applicableDiscounts, cartTotal } from './cart.js';

const ITEMS = [
  { id: 'a', name: 'Mug', price: 12.0, qty: 2, tags: ['kitchen'] },     // 24
  { id: 'b', name: 'Pan', price: 30.0, qty: 1, tags: ['kitchen'] },    // 30
  { id: 'c', name: 'Lamp', price: 18.0, qty: 1, tags: ['lighting'] }, // 18
];
// subtotal = 72.00

test('subtotal sums price * qty', () => {
  assert.equal(subtotal(ITEMS), 72.0);
});

test('applicableDiscounts respects minSubtotal', () => {
  const ds = [
    { code: 'BIG', percent: 30, requires: { minSubtotal: 100 } },
    { code: 'OK',  percent: 10, requires: { minSubtotal: 50 } },
  ];
  const apps = applicableDiscounts(ITEMS, ds);
  assert.deepEqual(apps.map((d) => d.code), ['OK']);
});

test('applicableDiscounts respects tag requirement', () => {
  const ds = [
    { code: 'KITCHEN', percent: 15, requires: { tag: 'kitchen' } },
    { code: 'PATIO',   percent: 25, requires: { tag: 'patio' } },
  ];
  const apps = applicableDiscounts(ITEMS, ds);
  assert.deepEqual(apps.map((d) => d.code), ['KITCHEN']);
});

// THE BUG-EXPOSING TEST: two stackable codes must NOT compound.
test('cartTotal applies only the highest discount, not their sum', () => {
  const ds = [
    { code: 'A20', percent: 20, requires: {} },
    { code: 'B20', percent: 20, requires: {} },
  ];
  // Subtotal 72, single 20% discount = 57.60, > 50 so no shipping.
  // BUG would compute: 72 * (1 - 40/100) = 43.20, < 50 so adds 5.00 = 48.20.
  const total = cartTotal(ITEMS, ds);
  assert.equal(total, 57.60);
});

test('cartTotal returns subtotal when no discount applies', () => {
  const ds = [{ code: 'NO', percent: 50, requires: { minSubtotal: 1000 } }];
  // Subtotal 72, no discount, 72 > 50 so no shipping.
  assert.equal(cartTotal(ITEMS, ds), 72.0);
});

test('cartTotal adds shipping when discounted subtotal drops below 50', () => {
  const ds = [{ code: 'HALF', percent: 50, requires: {} }];
  // Subtotal 72 * 0.5 = 36.00, < 50, +5 shipping = 41.00.
  assert.equal(cartTotal(ITEMS, ds), 41.0);
});

test('cartTotal picks highest discount when multiple apply', () => {
  const ds = [
    { code: 'P5',  percent: 5,  requires: {} },
    { code: 'P25', percent: 25, requires: {} },
    { code: 'P15', percent: 15, requires: {} },
  ];
  // Subtotal 72, 25% off = 54.00, > 50, no shipping.
  assert.equal(cartTotal(ITEMS, ds), 54.0);
});
