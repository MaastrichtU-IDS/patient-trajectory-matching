'use strict';
/* shared/auth.js (D50) in Node, with the browser's pieces stubbed: the PKCE redirect it builds, the code exchange
   on return (state checked, verifier sent), the bearer header it adds, the refresh on reload, and that it stays
   inert without a realm. No network: fetch is a stub that plays the server and Keycloak.

   node demos/mockups/test_auth_page.cjs */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

const SOURCE = fs.readFileSync(path.join(__dirname, 'shared/auth.js'), 'utf8');
const ISSUER = 'https://keycloak.example.test/realms/ids-apps';
const SITE = 'https://ptm.example.test';

function storage(initial = {}) {
  const data = {...initial};
  return {getItem: k => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); },
          removeItem: k => { delete data[k]; }, data};
}

function browser({href, session, authEnabled = true, tokenResponses = []}) {
  const calls = [];
  const location = new URL(href);
  const win = {
    assigned: null, replaced: null,
    location: Object.assign(location, {assign: url => { win.assigned = url; }, reload: () => { win.reloaded = true; }}),
    history: {replaceState: (a, b, url) => { win.replaced = url; }},
    sessionStorage: session,
    crypto: crypto.webcrypto, TextEncoder, URL, URLSearchParams, btoa, console,
    document: {currentScript: {src: `${SITE}/shared/auth.js`}},
    fetch: async (url, options = {}) => {
      url = String(url);
      calls.push({url, options});
      const json = (status, value) => ({ok: status < 400, status, json: async () => value});
      if (url === `${SITE}/api/auth-config`) {
        return authEnabled ? json(200, {enabled: true, issuer: ISSUER, client_id: 'ptm-mockups'}) : json(200, {enabled: false});
      }
      if (url === `${ISSUER}/protocol/openid-connect/token`) {
        const next = tokenResponses.shift();
        return next ? json(200, next) : json(400, {error: 'invalid_grant'});
      }
      return json(200, {ok: true});
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SOURCE, win);
  return {win, calls, auth: win.PtmAuth};
}

const tokenOf = (calls, i) => Object.fromEntries(new URLSearchParams(calls.filter(c => c.url.endsWith('/token'))[i].options.body));

(async () => {
  // 1. Sign-in builds a PKCE S256 redirect and keeps the verifier for this tab only.
  {
    const session = storage();
    const {win, auth} = browser({href: `${SITE}/store-query/`, session});
    await auth.ready;
    await auth.signIn();
    const target = new URL(win.assigned);
    assert.strictEqual(target.origin + target.pathname, `${ISSUER}/protocol/openid-connect/auth`);
    const q = target.searchParams;
    assert.strictEqual(q.get('response_type'), 'code');
    assert.strictEqual(q.get('client_id'), 'ptm-mockups');
    assert.strictEqual(q.get('redirect_uri'), `${SITE}/store-query/`);
    assert.strictEqual(q.get('code_challenge_method'), 'S256');
    const flow = JSON.parse(session.data['ptm.flow']);
    assert.strictEqual(flow.state, q.get('state'));
    const expected = crypto.createHash('sha256').update(flow.verifier).digest('base64url');
    assert.strictEqual(q.get('code_challenge'), expected);
    assert.ok(!q.has('code_verifier'), 'the verifier never leaves the tab');
    console.log('ok: the sign-in redirect is PKCE S256');

    // 2. Returning with the code: the state must match, the verifier is sent, the URL is cleaned.
    const back = browser({href: `${SITE}/store-query/?code=abc&state=${flow.state}&session_state=x`, session,
                          tokenResponses: [{access_token: 'AT1', expires_in: 300, refresh_token: 'RT1'}]});
    await back.auth.ready;
    const sent = tokenOf(back.calls, 0);
    assert.deepStrictEqual([sent.grant_type, sent.code, sent.code_verifier, sent.redirect_uri, sent.client_id],
                           ['authorization_code', 'abc', flow.verifier, `${SITE}/store-query/`, 'ptm-mockups']);
    assert.strictEqual(back.win.replaced, '/store-query/');
    assert.strictEqual(session.data['ptm.refresh'], 'RT1');
    assert.ok(!('ptm.flow' in session.data));
    assert.ok(!Object.values(session.data).includes('AT1'), 'the access token is kept in memory, not in storage');
    await back.auth.fetch(`${SITE}/api/datasets`);
    const last = back.calls[back.calls.length - 1];
    assert.strictEqual(last.options.headers.Authorization, 'Bearer AT1');
    console.log('ok: the code exchange checks the state, sends the verifier and adds the bearer header');

    // 3. A reload: the refresh token from this tab's storage gets a new access token.
    const reload = browser({href: `${SITE}/`, session, tokenResponses: [{access_token: 'AT2', expires_in: 300, refresh_token: 'RT2'}]});
    await reload.auth.ready;
    assert.deepStrictEqual([tokenOf(reload.calls, 0).grant_type, tokenOf(reload.calls, 0).refresh_token], ['refresh_token', 'RT1']);
    await reload.auth.fetch(`${SITE}/api/datasets`);
    assert.strictEqual(reload.calls[reload.calls.length - 1].options.headers.Authorization, 'Bearer AT2');
    assert.strictEqual(session.data['ptm.refresh'], 'RT2');
    console.log('ok: a reload refreshes from sessionStorage');
  }

  // 4. A returning code whose state does not match is not exchanged.
  {
    const session = storage({'ptm.flow': JSON.stringify({state: 'mine', verifier: 'v', redirect: `${SITE}/`})});
    const {calls, auth} = browser({href: `${SITE}/?code=abc&state=theirs`, session,
                                   tokenResponses: [{access_token: 'X', expires_in: 300}]});
    await auth.ready;
    assert.strictEqual(calls.filter(c => c.url.endsWith('/token')).length, 0);
    await auth.fetch(`${SITE}/api/datasets`);
    assert.ok(!calls[calls.length - 1].options.headers, 'no token, no header');
    console.log('ok: a mismatched state is never exchanged');
  }

  // 5. A refused refresh signs out locally; without a realm everything is plain fetch.
  {
    const session = storage({'ptm.refresh': 'stale'});
    const {auth} = browser({href: `${SITE}/`, session});
    await auth.ready;
    assert.ok(!('ptm.refresh' in session.data));
    const plain = browser({href: `${SITE}/`, session: storage(), authEnabled: false});
    await plain.auth.ready;
    assert.strictEqual(await plain.auth.enabled(), false);
    await plain.auth.fetch(`${SITE}/api/datasets`, {});
    assert.ok(!plain.calls[plain.calls.length - 1].options.headers);
    assert.strictEqual(await plain.auth.bar({}), null);
    console.log('ok: a refused refresh clears the tab; no realm means plain fetch');
  }
})().catch(error => { console.error(error); process.exit(1); });
