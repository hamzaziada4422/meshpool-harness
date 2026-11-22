const {chromium} = require("playwright");
(async () => {
  const b = await chromium.launch(); const p = await b.newPage(); const errs = [];
  p.on("pageerror", e => errs.push(e.message));
  await p.goto("http://127.0.0.1:8830/"); await p.waitForTimeout(1500);
  const r = await p.evaluate(async () => {
    const d = document.createElement("div"); document.body.appendChild(d);
    mdRender(d, "Flow:\n\n```mermaid\ngraph TD\n  A[Start] --> B{Ready?}\n  B -->|yes| C[Ship]\n```\n\nThe formula $$E = mc^2$$ and inline \\(a^2+b^2=c^2\\). Price is $5 and $6 today.\n\n```mermaid\nthis is not valid\n```");
    await new Promise(r => setTimeout(r, 5000));
    return {svg: d.querySelectorAll(".mermaid-box svg").length, katex: d.querySelectorAll(".katex").length, err: d.querySelectorAll(".mm-err").length, text: d.innerText.slice(0, 200)};
  });
  console.log(JSON.stringify(r), errs.join("|"));
  await b.close();
})();
