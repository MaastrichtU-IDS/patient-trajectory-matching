'use strict';
/* On a hosted copy (D50), say what bounds a store search: the server clamps each search to a budget of attempts per
   patient and stops a request at a time limit, so a search that reaches the budget is shown as unresolved (the
   search stopped before deciding), and one over the time limit is refused and matched in the browser instead.
   Locally, and on static hosting, nothing is shown. */
(function () {
  const root = new URL('..', document.currentScript ? document.currentScript.src : location.href);
  fetch(new URL('api/limits', root)).then(r => (r.ok ? r.json() : null)).then(limits => {
    const box = document.getElementById('hosted-limits');
    if (!box || !limits || !limits.hosted) return;
    const fmt = n => Number(n).toLocaleString('en');
    box.textContent = `On this hosted copy each store search is limited to ${fmt(limits.search_budget)} attempts per patient`
      + (limits.match_time_limit_s ? ` and ${limits.match_time_limit_s} s per query` : '')
      + `, with at most ${limits.max_steps} steps and ${limits.max_relaxations} relaxations. A patient whose search reaches`
      + ' the limit is shown as unresolved (the search stopped before deciding); a query over the time limit is matched'
      + ' in the browser instead.';
    box.hidden = false;
  }).catch(() => {});
})();
