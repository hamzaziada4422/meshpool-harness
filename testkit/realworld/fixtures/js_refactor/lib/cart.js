// Shopping cart totals. Prices are in cents.
var SHIPPING_FREE_FROM = 5000;

function lineTotal(line) {
  var t = line.price * line.qty;
  if (line.region === 'EU') {
    t = t + Math.round(t * 0.2);
  } else if (line.region === 'UK') {
    t = t + Math.round(t * 0.2);
  } else if (line.region === 'US') {
    t = t + Math.round(t * 0.07);
  }
  return t;
}

function cartTotal(cart) {
  var sum = 0;
  for (var i = 0; i < cart.lines.length; i++) {
    var line = cart.lines[i];
    var t = line.price * line.qty;
    if (cart.region === 'EU') {
      t = t + Math.round(t * 0.2);
    } else if (cart.region === 'UK') {
      t = t + Math.round(t * 0.2);
    } else if (cart.region === 'US') {
      t = t + Math.round(t * 0.07);
    }
    sum = sum + t;
  }
  var shipping = sum >= SHIPPING_FREE_FROM || sum === 0 ? 0 : 495;
  return sum + shipping;
}

function summary(cart) {
  var total = cartTotal(cart);
  var count = 0;
  for (var i = 0; i < cart.lines.length; i++) {
    count = count + cart.lines[i].qty;
  }
  return count + ' items, ' + (total / 100).toFixed(2);
}

module.exports = { lineTotal: lineTotal, cartTotal: cartTotal, summary: summary };
