# Refactor request

`lib/cart.js` has the tax logic copied twice and uses `var` everywhere. Please refactor it:

- Move the tax calculation into a new module `lib/tax.js` that exports `taxFor(amountCents, region)`, returning
  the tax in cents (20% for EU and UK, 7% for US, 0 otherwise, rounded with `Math.round`). Add Canada: region
  `CA` has 5%.
- `lib/cart.js` must use `taxFor` (require it from `./tax`) for both line and cart totals; no tax rates may stay
  in `cart.js`.
- Replace every `var` in `lib/cart.js` with `const` or `let`.
- Keep the exported API of `lib/cart.js` (`lineTotal`, `cartTotal`, `summary`) and its behaviour exactly the
  same, apart from the new CA rate. The existing tests (`npm test`) must keep passing; do not change them.
