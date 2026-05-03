// A shopping-cart utility module. Used by the storefront's checkout page.
//
// Invariants documented in the team handbook:
//   * Discount codes are MUTUALLY EXCLUSIVE — only the highest-value
//     applicable code applies, never combined.
//   * The "free shipping" line item is computed AFTER the discount, so a
//     discount that drops the subtotal below the free-shipping threshold
//     legitimately removes shipping.
//   * A code is "applicable" iff every requirement on it is met.

/** @typedef {{ id: string, name: string, price: number, qty: number, tags?: string[] }} Item */
/** @typedef {{ code: string, percent: number, requires?: { minSubtotal?: number, tag?: string } }} Discount */

/** Sum the cart line items (price * qty). */
export function subtotal(items) {
  return items.reduce((acc, it) => acc + it.price * it.qty, 0);
}

/** Return the list of discounts that apply to the given cart. */
export function applicableDiscounts(items, discounts) {
  const sub = subtotal(items);
  const tags = new Set(items.flatMap((it) => it.tags || []));
  return discounts.filter((d) => {
    const req = d.requires || {};
    if (req.minSubtotal != null && sub < req.minSubtotal) return false;
    if (req.tag != null && !tags.has(req.tag)) return false;
    return true;
  });
}

/**
 * Apply at most ONE discount code to the cart and return the cart total.
 *
 * Rules:
 *   - The single highest-percent applicable discount wins.
 *   - If no codes apply, return the bare subtotal.
 *   - Free shipping ($5.00) is added to the total whenever the discounted
 *     subtotal is < $50, otherwise shipping is free.
 *
 * Currency is always rounded to two decimal places.
 *
 * BUG: this currently SUMS the percent reductions of every applicable
 * discount instead of picking just the largest one. Two stackable 20%
 * codes wrongly drop the cart 40% rather than 20%.
 */
export function cartTotal(items, discounts) {
  const sub = subtotal(items);
  const applicable = applicableDiscounts(items, discounts);
  let pct = 0;
  for (const d of applicable) {
    pct += d.percent;
  }
  const discounted = sub * (1 - pct / 100);
  const shipping = discounted < 50 ? 5.0 : 0;
  return Math.round((discounted + shipping) * 100) / 100;
}
