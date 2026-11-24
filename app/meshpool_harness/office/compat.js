/* Plain old JavaScript on purpose: it runs in every Office webview. When taskpane.js could not run (Office 2016 or
   2019 with Internet Explorer's engine), the panel says which Office versions work instead of staying empty. */
(function () {
  window.setTimeout(function () {
    if (window.__mpParsed) { return; }
    document.body.innerHTML = '<div class="off"><h2>This version of Office is too old for meshpool</h2>' +
      '<p>The meshpool panel needs Microsoft 365, Office 2021 or later, or Office on the web.</p></div>';
  }, 1500);
}());
