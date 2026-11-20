'use strict';
/* Sign-in for the mockups when the server is configured with a realm (D50, docs/hosting-plan.md): the OIDC
   authorization-code flow with PKCE (S256) against the public client the server names at /api/auth-config.
   No client secret exists, and no library is vendored.

   Where the tokens live: the access token in memory only, so no script reading storage later can lift it, and it
   dies with the tab; the refresh token in sessionStorage (this tab only, cleared when the tab closes), so a reload
   or a move to another page of the site gets a fresh access token without a new round trip to Keycloak's login.
   Memory alone would sign the user out on every page change, since every mockup page is a separate document.

   Without a realm (local use, static hosting) everything here is inert: `PtmAuth.fetch` is plain `fetch`, and the
   bar shows nothing. */
(function () {
  const ROOT = new URL('..', document.currentScript ? document.currentScript.src : location.href);
  const REFRESH_KEY = 'ptm.refresh';
  const FLOW_KEY = 'ptm.flow';
  let config = null;         // /api/auth-config, once read
  let access = null;         // {token, expires} in memory
  let idToken = null;        // in memory, only as the logout hint
  let ready = null;          // the promise of init()

  const b64url = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const random = n => b64url(crypto.getRandomValues(new Uint8Array(n)));
  const endpoint = name => `${config.issuer}/protocol/openid-connect/${name}`;
  // The page's canonical address, the redirect URI Keycloak knows exactly (no wildcard): /index.html becomes /.
  const here = () => location.origin + location.pathname.replace(/index\.html$/, '');

  async function token(params) {
    const response = await fetch(endpoint('token'), {
      method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: new URLSearchParams({client_id: config.client_id, ...params})});
    if (!response.ok) throw new Error('TOKEN_REQUEST_REFUSED');
    const value = await response.json();
    access = {token: value.access_token, expires: Date.now() + (value.expires_in - 20) * 1000};
    if (value.id_token) idToken = value.id_token;
    // The realm rotates refresh tokens (each works once), so the newest replaces the last.
    if (value.refresh_token) sessionStorage.setItem(REFRESH_KEY, value.refresh_token);
  }

  async function refresh() {
    const saved = sessionStorage.getItem(REFRESH_KEY);
    if (!saved) { access = null; return false; }
    try {
      await token({grant_type: 'refresh_token', refresh_token: saved});
      return true;
    } catch (error) {
      sessionStorage.removeItem(REFRESH_KEY); access = null;
      return false;
    }
  }

  async function finishSignIn() {
    const params = new URLSearchParams(location.search);
    const flow = JSON.parse(sessionStorage.getItem(FLOW_KEY) || 'null');
    if (!params.has('state') || !flow) return;
    sessionStorage.removeItem(FLOW_KEY);
    const clean = new URL(location.href);
    ['code', 'state', 'session_state', 'iss', 'error', 'error_description'].forEach(k => clean.searchParams.delete(k));
    history.replaceState(null, '', clean.pathname + clean.search + clean.hash);
    if (params.get('state') !== flow.state || !params.get('code')) return;  // another tab's, a replay, or an error
    await token({grant_type: 'authorization_code', code: params.get('code'), redirect_uri: flow.redirect,
                 code_verifier: flow.verifier});
  }

  async function init() {
    try {
      const response = await fetch(new URL('api/auth-config', ROOT));
      config = response.ok ? await response.json() : {enabled: false};
    } catch (error) {
      config = {enabled: false};  // static hosting, or no server: no sign-in
    }
    if (!config.enabled) return config;
    try { await finishSignIn(); } catch (error) { access = null; }
    if (!access) await refresh();
    return config;
  }

  async function signIn() {
    await ready;
    const verifier = random(32);
    const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
    const state = random(16);
    sessionStorage.setItem(FLOW_KEY, JSON.stringify({state, verifier, redirect: here()}));
    location.assign(endpoint('auth') + '?' + new URLSearchParams({
      response_type: 'code', client_id: config.client_id, redirect_uri: here(), scope: 'openid', state,
      code_challenge: challenge, code_challenge_method: 'S256'}));
  }

  /* Sign out: revoke the refresh token at the realm, forget both tokens, then end the realm session through its
     logout endpoint (with the ID token as hint when held, else the client id) and come back here. */
  async function signOut() {
    const saved = sessionStorage.getItem(REFRESH_KEY);
    sessionStorage.removeItem(REFRESH_KEY); access = null;
    if (saved) {
      try {
        await fetch(endpoint('revoke'), {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body: new URLSearchParams({client_id: config.client_id, token: saved, token_type_hint: 'refresh_token'})});
      } catch (error) { /* the logout below still ends the session */ }
    }
    const params = {client_id: config.client_id, post_logout_redirect_uri: here()};
    if (idToken) params.id_token_hint = idToken;
    idToken = null;
    location.assign(endpoint('logout') + '?' + new URLSearchParams(params));
  }

  /* fetch with the bearer token when signed in; one refresh and retry on an expired or refused token. */
  async function authFetch(url, options = {}) {
    await ready;
    if (!config.enabled) return fetch(url, options);
    if (access && Date.now() > access.expires) await refresh();
    const send = () => fetch(url, access ? {...options, headers: {...(options.headers || {}), Authorization: `Bearer ${access.token}`}} : options);
    let response = await send();
    if (response.status === 401 && access) {
      const retried = await refresh();
      response = retried ? await send() : await fetch(url, options);
    }
    return response;
  }

  async function datasets() {
    const response = await authFetch(new URL('api/datasets', ROOT));
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  const REFUSALS = {
    LOGIN_REQUIRED: 'This dataset needs a signed-in account that the owner has approved.',
    NOT_APPROVED: 'Your account is awaiting approval by the owner. You will be able to use this dataset once it is approved.',
    ROLE_REQUIRED: 'This dataset needs a credentialed-access role, granted by the owner after checking your own credentialing.',
    NOT_AVAILABLE: 'No dataset for this page is available to your account.',
    TOKEN_INVALID: 'Your sign-in has expired. Sign in again.',
    AUTH_UNAVAILABLE: 'The sign-in service is not reachable right now. Try again shortly.',
  };

  /* A small bar: sign-in state and, once the dataset list is read, whether the account awaits approval. */
  async function bar(element) {
    await ready;
    if (!config.enabled || !element) return null;
    let listing = null;
    try { listing = await datasets(); } catch (error) { listing = null; }
    const caller = listing ? listing.caller : {signed_in: false, approved: false};
    element.innerHTML = '';
    const text = document.createElement('span');
    text.textContent = caller.signed_in
      ? (caller.approved ? 'Signed in.' : 'Signed in. Your account is awaiting approval by the owner; until then only public datasets are shown.')
      : 'Not signed in: public datasets only.';
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'btn';
    button.textContent = caller.signed_in ? 'Sign out' : 'Sign in or register';
    button.addEventListener('click', caller.signed_in ? signOut : signIn);
    element.append(text, ' ', button);
    element.hidden = false;
    return listing;
  }

  ready = init();
  window.PtmAuth = {ready, fetch: authFetch, datasets, signIn, signOut, bar, refusal: code => REFUSALS[code] || null,
                    enabled: async () => (await ready).enabled === true};
})();
