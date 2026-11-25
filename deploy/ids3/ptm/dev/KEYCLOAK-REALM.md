# Keycloak: the shared user realm (checklist for the realm admin)

Not created yet (phase 2). One open realm for all the owner's user-facing apps, on the platform Keycloak
(<https://keycloak.k8s.semanticscience.org>), **separate from the infrastructure realm `ids3`**. ptm is its first
client; other apps add their own clients to the same realm. Because the realm is shared, this file belongs beside
`ids3/platform/keycloak/` in the services repo rather than under one project; it sits here until that PR is written.

Owner decisions (2026-11-17 to 2026-11-20) this implements: self-registration by email or through ORCID, GitHub and
Google; several providers linkable to one account; no roles for a new account until the owner adds it to the
`approved` group; the owner emailed on each registration; dataset roles granted by hand.

The realm is **`ids-apps`** (decided 2026-11-20); its name is load-bearing, since it is in every issuer URL. Values to
settle before starting:

| Placeholder | Meaning | Working value |
|---|---|---|
| `OWNER_EMAIL` | the owner's address, for realm admin and registration notices | to confirm |
| `BACKUP_ADMIN` | a second realm admin | **owner to name** (not yet decided) |
| `MAIL_FROM` | sender for verification and notice emails | `info-ids@maastrichtuniversity.nl` (as ontoexplorer) |

Switch the realm selector (top left) to `ids-apps` before every step after the first. Work done in `master` or `ids3`
has no effect here.

## Two ways to set it up

**(A) Import the realm file, then add what a file must not hold.** `deploy/ids3/keycloak/ids-apps-realm.json` holds
sections 1 to 5 below as code. It contains no secret and no user, and its provider client ids are placeholders.
It is generated: edit `deploy/ids3/keycloak/gen_realm.py` and re-run it, never the JSON by hand; `test_datasets`
fails if the committed file differs from the generator's output by a byte (`gen_realm.py --check` says the same).
`deploy/ids3/keycloak/check_realm.py` checks it offline against these sections and against Keycloak 26's import
format (the representation classes of the platform's version, 26.8.1).
1. Admin console of the `master` realm → realm selector → **Create realm** → **Resource file**: upload
   `ids-apps-realm.json` → Create. The realm name comes from the file.
2. **Identity providers** → for each of `github`, `google` and `orcid`: replace the `REPLACE_WITH_…` client id with
   the one the owner registered, and enter the **client secret** (section 4 gives the redirect URI to register).
   Save. A provider whose secret is missing simply fails at login; nothing else breaks.
3. **Realm settings → Email → Test connection.** Section 1 explains the SMTP egress check.
4. **Users:** give the owner (`OWNER_EMAIL`) and `BACKUP_ADMIN` the client role `realm-management` → `realm-admin`
   (section 5). The file creates no user.
5. Check that the browser flow is `browser with admin OTP` (Authentication → Flows), and that each identity
   provider's "Post login flow" is `post broker admin OTP`, and that the reset credentials flow is `reset
   credentials, not for admins`. An admin login asks for OTP, whether by password or through a provider; a non-admin
   login does not.
6. Section 6 (the registration notice) is a platform-side poller, outside the file.
7. Verify as in section 7.

If the import is refused, or a setting differs after import, follow (B) for that part. The file is the reference
for the values either way.

**(B) Manual steps:** sections 1 to 5 below, in the admin console.

## 1. Realm

- [ ] Create realm `ids-apps` (Realm name is load-bearing: it is in `PTM_OIDC_ISSUER` and `PTM_OIDC_JWKS_URI`).
- [ ] **Realm settings → General:** Display name "Semantic Science apps" (or the owner's choice); Unmanaged
      attributes off.
- [ ] **Realm settings → Login:** User registration **on**; Forgot password **on**; Remember me on; Email as username
      **off**; Login with email **on**; Duplicate emails **off**; Verify email **on**; Edit username off.
- [ ] **Realm settings → Email:** host `smtp.maastrichtuniversity.nl`, port `25`, no SSL, no StartTLS, no
      authentication (the MU relay, unauthenticated on :25, as ontoexplorer and realm-os use); From `MAIL_FROM`.
      Send a test email. **Confirm Keycloak's pod can reach `137.120.1.21:25`**: if the `platform` namespace is
      egress-locked, it needs an SMTP allow like `ids3/projects/ontoexplorer/dev/allow-smtp-egress.yaml`, selecting
      `app: keycloak`. Without it, verification emails fail silently.
- [ ] **Realm settings → Security defenses → Brute force detection:** on (lockout temporary, defaults).
- [ ] **Authentication → Policies → Password policy:** minimum length 12, not username, not email, password history 3.
- [ ] **Realm settings → Sessions / Tokens:** access token lifespan 5 min (default); SSO session idle 30 min, max
      10 h; offline sessions not used (see default roles below). **Revoke Refresh Token: on, Refresh Token Max
      Reuse: 0** (rotation: each refresh token works once, so a stolen one is useless after the page's next refresh,
      and its replay ends the session). The page keeps its refresh token in the tab's `sessionStorage`; rotation is
      what bounds that exposure.
- [ ] *Option, not required:* DPoP-bound tokens for `ptm-mockups` (client → Advanced → "Require DPoP bound tokens",
      supported in Keycloak 26). It would bind tokens to a key held by the tab; it needs page and server support
      that are not built.
- [ ] **Realm settings → User profile:** first and last name optional; email required for email registrations
      (ORCID often returns no email; see section 4).
- [ ] **Realm settings → Events → User events settings:** save events **on**, expiration 90 days, include at least
      `REGISTER`, `LOGIN`, `LOGIN_ERROR`, `UPDATE_EMAIL`, `VERIFY_EMAIL`, `IDENTITY_PROVIDER_LINK_ACCOUNT`,
      `REMOVE_FEDERATED_IDENTITY`, `DELETE_ACCOUNT`, and for admin forensics `UPDATE_TOTP`, `REMOVE_TOTP`,
      `UPDATE_CREDENTIAL`, `REMOVE_CREDENTIAL`, `UPDATE_PASSWORD`, `RESET_PASSWORD`, `SEND_RESET_PASSWORD`,
      `IDENTITY_PROVIDER_LOGIN`, `IDENTITY_PROVIDER_FIRST_LOGIN`. **Admin events:** save on, include representation off (so role
      and group changes are recorded without copying user details), expiration 1 year: this is the record of who
      was approved and who was granted a dataset role, and when.

## 2. Roles and groups: new accounts get nothing

- [ ] **Realm settings → User registration → Default roles** (`default-roles-ids-apps`): remove `offline_access` and
      `uma_authorization`. Keep only the `account` client's `view-profile` and `manage-account` (users need them to
      open the account console and link providers). A new account therefore holds no app role.
- [ ] **Realm roles → Create role** `approved` ("approved by the owner; required by every non-public dataset").
- [ ] **No other realm role.** The shared realm holds broad grants only (`approved` and the admin roles; D56,
      MaastrichtU-IDS/services#751). Detailed grants, such as full MIMIC-IV, live in the app that needs them:
      ptm will keep its own grant store and append-only grant log when full MIMIC-IV is hosted, after the storage
      and backup access review (hosting plan, section 7). Until then nobody can hold a full MIMIC-IV grant.
- [ ] **Groups → Create group** `approved`; **Role mapping → Assign role** `approved`. Approving a user is adding
      them to this group; removing them withdraws access at their next token (at most 5 min).
- [ ] Do **not** put `approved` in the default roles, and do not enable any "default group".
- [ ] **Credentialing checks are not recorded in Keycloak.** The owner's check of a user's PhysioNet credentialing
      and signed MIMIC-IV data use agreement is recorded in ptm, with the grant it supports, when full MIMIC-IV is
      built (D56). No PhysioNet user attributes in the realm.

## 3. The ptm client

- [ ] **Clients → Create client** `ptm-mockups` (must equal `PTM_OIDC_CLIENT_ID`):
  - Client authentication **off** (public browser client, PKCE); Standard flow **on**; Direct access grants
    **off**; Implicit **off**; Service accounts **off**; OAuth 2.0 Device flow off.
  - Root URL `https://ptm.k8s.semanticscience.org`.
  - Valid redirect URIs, **exactly these two, no wildcard** (the pages that sign in; `shared/auth.js` sends the page's
    canonical address): `https://ptm.k8s.semanticscience.org/` and
    `https://ptm.k8s.semanticscience.org/store-query/`.
  - Valid post logout redirect URIs: the same two.
  - Web origins `https://ptm.k8s.semanticscience.org` (exact origin, no trailing slash).
  - A page that starts signing in must be added to both lists, or Keycloak refuses with `invalid_redirect_uri`.
  - Advanced → Proof Key for Code Exchange Code Challenge Method **S256**.
- [ ] **Audience mapper:** `ptm-mockups` → Client scopes → `ptm-mockups-dedicated` → Add mapper → By configuration
      → Audience: name `ptm-api-audience`, Included Custom Audience `ptm-api` (must equal `PTM_OIDC_AUDIENCE`), add
      to access token **on**, to ID token off. The server refuses any token without it.
- [ ] **Realm roles in the access token:** the default `roles` client scope must stay assigned to the client (it
      puts `realm_access.roles` in the access token, which is where the server reads `approved`).
      Check with Client scopes → Evaluate → a test user → Generated access token.
- [ ] Keep the default `basic` client scope on the client (it puts `sub` in the access token, which the request log
      records) and do not enable "lightweight access tokens": the server requires the access token's `typ` claim
      (`Bearer`), `iat`, `exp`, `iss` and `aud`.
- [ ] **Full scope allowed off** (D56, services#751): `ptm-mockups` → Client scopes → `ptm-mockups-dedicated` →
      Scope → Full scope allowed **off**, then Assign role → realm role `approved` only. A ptm token then carries no
      other app's roles, whatever else the shared realm holds. Evaluate (above) must show `realm_access.roles`
      containing `approved` for an approved test user, and nothing else.

## 4. Identity providers (brokering) and account linking

Each provider needs an OAuth app registered with the provider by the owner, using the redirect URI
`https://keycloak.k8s.semanticscience.org/realms/ids-apps/broker/<alias>/endpoint`. Client secrets go into Keycloak's
identity-provider form (its database), never into git or this repo. Keycloak's outbound calls to the providers
already go through the egress-proxy (`KC_SPI_CONNECTIONS_HTTP_CLIENT_DEFAULT_PROXY_MAPPINGS` in `keycloak.yaml`).

What the owner registers with each provider:

| Provider | Where to register | Redirect / callback URI to enter | Notes |
|---|---|---|---|
| GitHub | GitHub → Settings → Developer settings → OAuth Apps (or an org's OAuth App) | `https://keycloak.k8s.semanticscience.org/realms/ids-apps/broker/github/endpoint` | Homepage URL `https://ptm.k8s.semanticscience.org` |
| Google | Google Cloud console → APIs & Services → Credentials → OAuth client ID, type "Web application" | `https://keycloak.k8s.semanticscience.org/realms/ids-apps/broker/google/endpoint` | Consent screen: scopes `openid`, `email`, `profile` |
| ORCID | ORCID → Developer tools (public API client) | `https://keycloak.k8s.semanticscience.org/realms/ids-apps/broker/orcid/endpoint` | ORCID usually returns no email; Keycloak then asks for one and verifies it |

Each yields a client id and a client secret. They go into the identity-provider form in Keycloak, not into git.

- [ ] **GitHub** (built-in provider, alias `github`): scope `user:email`. Trust email **off**.
- [ ] **Google** (built-in, alias `google`): Trust email **off** too (security review of phase 2): every address is
      verified by Keycloak itself, whatever the provider claims.
- [ ] **ORCID** (OpenID Connect v1.0 provider, alias `orcid`): discovery
      `https://orcid.org/.well-known/openid-configuration`, client authentication "client secret sent as post",
      scope `openid`. Trust email **off**; ORCID usually returns no email, so first login asks for one and Verify
      email applies.
- [ ] For each: Sync mode `import`; First login flow `first broker login`; **Post login flow `post broker admin
      OTP`** (section 5); Hide on login page off.
- [ ] **Authentication → first broker login** (keep the built-in flow): Review profile `missing`; "Create user if
      unique"; on a clash, "Confirm link existing account" then "Verify existing account by email" **or**
      re-authentication. Never auto-link by email: with Trust email off, an attacker controlling an unverified
      address at a provider must not inherit an account.
- [ ] **Linking more providers to one account:** users do it themselves in the account console
      (`https://keycloak.k8s.semanticscience.org/realms/ids-apps/account` → Account security → Linked accounts). Nothing
      to configure beyond keeping `manage-account` in the default roles.

## 5. MFA for admins

- [ ] Give the owner (`OWNER_EMAIL`) and `BACKUP_ADMIN` realm administration: Users → user → Role mapping → assign
      client role `realm-management` → `realm-admin`. They administer at
      `https://keycloak.k8s.semanticscience.org/admin/ids-apps/console/`, with no `master` realm access.
- [ ] **The admin-capable roles** (security review of phase 2 and its re-check): every `realm-management` role
      that can grant `approved` or an app's role, change who may sign in, or act as another user:
      `realm-admin` (which includes all the others), `manage-users` (membership and role mappings),
      `manage-realm` (roles, groups and flows), `manage-clients`, `manage-identity-providers` and
      `impersonation`. Their holders must use OTP at every login and cannot reset their credentials by email.
- [ ] **Nobody holds any other `realm-management` role except read-only ones** (`view-*`, `query-*`). Before
      granting any other, add it to the list above: to `ADMIN_ROLES` in `deploy/ids3/keycloak/gen_realm.py` (the OTP
      conditions and the reset denial) and in `deploy/ids3/keycloak/check_realm.py`, which checks the import for
      exactly this. If fine-grained admin
      permissions are ever enabled (services#751 plans them for per-app admins), any role they let assign `approved`
      joins the list, and their holders' query roles get the same OTP and no-reset rules.
- [ ] **Authentication → Flows → duplicate `browser`** as `browser with admin OTP`. Under the username/password form,
      add one CONDITIONAL subflow per admin-capable role, in the order above, each ending in the OTP form
      (Required). Subflow *k* has "Condition - user role" for role *k*, plus the same condition with *negate* on for
      each role before it, so an admin is asked once (conditions within a subflow are ANDed).

      Bind it as the browser flow. Admins then enrol an authenticator (the "Configure OTP" required action is
      triggered automatically) and use it at every login; other users are not asked.
- [ ] **The same after an identity-provider login** (security review of phase 2, M2). Without it, an admin who
      linked GitHub, Google or ORCID could sign in through the provider and skip OTP. Create a top-level flow
      `post broker admin OTP` with the same CONDITIONAL subflows, and set it as **Post login flow** on all three
      providers.
- [ ] **Admins keep local credentials and do not link identity providers.** An admin account signs in with its
      username, a strong local password and OTP. The post-broker flow covers a linked provider anyway, but an
      unlinked admin account has no provider in its trust chain at all.
- [ ] **No reset by email for admins** (the re-check's medium). Keycloak's default "reset credentials" flow ends in
      "Reset - Conditional OTP", whose `reset-otp` step, unconfigured, only adds "Configure OTP" and succeeds. So
      whoever controlled an admin's mailbox could set a new password, enrol their own authenticator and reach the
      admin console: OTP did **not** stand in the way. The realm therefore binds its own **Reset credentials** flow,
      `reset credentials, not for admins`:
      1. "Choose user", then "Send reset email" (the link is mailed to everyone alike, so the form does not reveal
         who is an admin);
      2. one CONDITIONAL subflow per admin-capable role: "Condition - user role" for that role, then **Deny
         access**, so following the link as an admin changes nothing;
      3. "Reset password", for everyone else. There is no OTP reset step: nobody resets OTP by email.

      Ordinary users keep self-service password reset. `RESET_PASSWORD`, `SEND_RESET_PASSWORD`, `UPDATE_TOTP` and
      `REMOVE_TOTP` stay audited.
- [ ] **How an admin recovers:** through another admin. The owner and `BACKUP_ADMIN` can each reset the other's
      password (Users → user → Credentials → Reset password, temporary) and remove a lost authenticator (Credentials
      → delete the OTP credential, so "Configure OTP" is asked at the next login), in the admin console. That is
      why a backup admin must exist before the realm goes live. If both are locked out, the platform admin
      recovers from the `master` realm.
- [ ] Optionally also enable WebAuthn (passkeys) as an alternative second factor for admins.
- [ ] Required actions enabled: Verify Email, Configure OTP, Update Password, Terms and Conditions (enable when the
      owner provides the text; default off).

## 6. Registration notice to the owner: a platform-side poller (built by the platform admin)

Decided 2026-11-20 (the owner is emailed at every new registration, to approve or ignore it); mechanism revised
2026-11-25 on the platform admin's proposal in MaastrichtU-IDS/services#751 (D56), replacing the earlier
event-listener plugin. Nothing in this repository implements it.

**Mechanism (platform admin):** a small poller reads Keycloak's database through a **read-only database role** that
can select only from a **view** filtered to realm `ids-apps` and event type `REGISTER`. It therefore holds no
Keycloak credential and sees no `ids3` (or other realm) events. It mails the owner through the MU relay
(`smtp.maastrichtuniversity.nl:25`, an SMTP egress allow in its namespace).

**What the realm must provide (already in `ids-apps-realm.json`):** "Save events" on, `REGISTER` among the saved
event types, user events kept 90 days (longer than any poller outage that matters).

**What ptm asks of the notice:**
- one email per registration, never repeated after a restart, none missed (the poller keeps its position, for example
  the last event time and id);
- contents: realm, username, email (or "none given"), how they registered (email or which provider), time (UTC), and
  the admin console link `https://keycloak.k8s.semanticscience.org/admin/ids-apps/console/#/ids-apps/users/<id>/settings`;
- `REGISTER` fires **before** email verification, so the email says the address was unverified at registration;
  the owner approves only after Users shows it verified;
- never a token, code, password, IP address or the event's other details.

Until the poller exists, the owner finds new accounts in Users, sorted by creation, or in Events filtered by
`REGISTER`.

## 7. Verify

- [ ] Register a test account by email: verification email arrives; after verifying, a token from the
      `ptm-mockups` client has `aud` containing `ptm-api` and `realm_access.roles` without `approved`.
- [ ] `GET https://ptm.k8s.semanticscience.org/api/datasets` with that token lists only public datasets and says
      the account awaits approval. Add it to `approved`, sign in again: the answer lists approved datasets (none on
      dev today).
- [ ] Link GitHub to the same account from the account console; sign in with GitHub; same user.
- [ ] An admin login asks for OTP.
- [ ] "Forgot password" for the test account resets its password; for an admin account, following the mailed link
      is denied and the password and authenticator are unchanged.
- [ ] The registration notice reached the owner, and says the address was unverified (once section 6 is built).
