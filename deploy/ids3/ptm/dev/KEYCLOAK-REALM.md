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
   provider's "Post login flow" is `post broker admin OTP`. An admin login asks for OTP, whether by password or
   through a provider; a non-admin login does not.
6. Section 6 (the registration notice) is a platform change, outside the file.
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
      `app: keycloak`. Without it, verification emails and the registration notice fail silently.
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
- [ ] **Realm roles → Create role** `dataset:mimic-iv:read` ("full MIMIC-IV: granted only after the owner has
      verified and recorded the user's own PhysioNet credentialing"). Create it now, assign it to nobody: the server
      refuses to serve full MIMIC-IV until the storage and backup access review (hosting plan, section 7).
      Later dataset roles follow the pattern `dataset:<dataset-id>:read`.
- [ ] **Groups → Create group** `approved`; **Role mapping → Assign role** `approved`. Approving a user is adding
      them to this group; removing them withdraws access at their next token (at most 5 min).
- [ ] Do **not** put `approved` or any dataset role in the default roles, and do not enable any "default group".
- [ ] **Recording a credentialing check** (owner): before assigning `dataset:mimic-iv:read`, check the user's
      PhysioNet credentialed status and their signed MIMIC-IV data use agreement on PhysioNet, then record it on the
      user (Users → user → Attributes, admin-only attributes): `physionet_username`, `physionet_verified_on`
      (date), `physionet_verified_by`. Mark these attributes as admin-edit only in the user profile (users cannot
      view or edit). The admin event of the role grant records when and by whom.

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
      puts `realm_access.roles` in the access token, which is where the server reads `approved` and dataset roles).
      Check with Client scopes → Evaluate → a test user → Generated access token.
- [ ] Keep the default `basic` client scope on the client (it puts `sub` in the access token, which the request log
      records) and do not enable "lightweight access tokens": the server requires the access token's `typ` claim
      (`Bearer`), `iat`, `exp`, `iss` and `aud`.
- [ ] Full scope allowed may stay on for now (the realm's realm roles are all app-facing). When a second app joins
      and has roles ptm should not see, turn it off and add `approved` and the dataset roles to the client's scope.

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
- [ ] **Who must use OTP:** everyone who can change group membership or grant roles, which means `approved` and
      the `dataset:*` roles. That is holders of `realm-management` → `manage-users`, which governs membership and
      role mappings, or `manage-realm`, which governs the roles and groups themselves. `realm-admin` includes both.
      If fine-grained admin permissions are ever enabled, any role they let assign `approved` or `dataset:*` must be
      added to the conditions.
- [ ] **Authentication → Flows → duplicate `browser`** as `browser with admin OTP`. Under the username/password form,
      add two CONDITIONAL subflows, each ending in the OTP form (Required):
      1. "Condition - user role" `realm-management.manage-users`;
      2. "Condition - user role" `realm-management.manage-realm` **and** "Condition - user role"
         `realm-management.manage-users` with *negate* on, so nobody is asked twice.

      Bind it as the browser flow. Admins then enrol an authenticator (the "Configure OTP" required action is
      triggered automatically) and use it at every login; other users are not asked.
- [ ] **The same after an identity-provider login** (security review of phase 2, M2). Without it, an admin who
      linked GitHub, Google or ORCID could sign in through the provider and skip OTP. Create a top-level flow
      `post broker admin OTP` with the same two CONDITIONAL subflows, and set it as **Post login flow** on all three
      providers.
- [ ] **Admins keep local credentials and do not link identity providers.** An admin account signs in with its
      username, a strong local password and OTP. The post-broker flow covers a linked provider anyway, but an
      unlinked admin account has no provider in its trust chain at all.
- [ ] **Residual: password reset by email.** "Forgot password" mails a reset link, so whoever controls an admin's
      mailbox can set a new password. OTP still stands between that and the admin console, which is why admins
      rely on OTP plus a strong local password, and why `RESET_PASSWORD`, `SEND_RESET_PASSWORD` and `UPDATE_TOTP` /
      `REMOVE_TOTP` are audited events. Turning "Forgot password" off for the whole realm would cost every user the
      reset, so it stays on.
- [ ] Optionally also enable WebAuthn (passkeys) as an alternative second factor for admins.
- [ ] Required actions enabled: Verify Email, Configure OTP, Update Password, Terms and Conditions (enable when the
      owner provides the text; default off).

## 6. Registration notice to the owner: an event-listener provider (PLATFORM CHANGE, needs the platform admin)

Decided 2026-11-20: option (a). The owner is emailed at every new registration, to approve or ignore it. This is a
**specification for the platform admin**: it changes the platform Keycloak's Deployment (`ids3/platform/keycloak/`),
which every realm, including `ids3`, depends on, so only the platform admin builds, reviews and deploys it. Nothing in
this repository implements it.

**What it does.** On a user event of type `REGISTER` in a realm where the listener is enabled, send one plain-text
email to a configured recipient. Nothing else: no other event type, no admin events, no outbound call except mail.

**Implementation (a small Java provider in its own repository, e.g. `maastrichtu-ids/keycloak-registration-notice`):**
- `RegistrationNoticeProviderFactory implements EventListenerProviderFactory`, id `registration-notice`; reads its
  configuration in `init(Config.Scope)`: `recipient` (required; an email address) and `admin-console-base`
  (default `https://keycloak.k8s.semanticscience.org/admin/master/console/`). A missing or malformed recipient
  disables the listener with one warning in the log, never a startup failure.
- `RegistrationNoticeProvider implements EventListenerProvider`: in `onEvent(Event)`, return unless
  `event.getType() == EventType.REGISTER`. Look up the user (`session.users().getUserById(realm, event.getUserId())`);
  build the message from: realm name, username, email (or "none given"), identity provider (`event.getDetails()`
  key `identity_provider`, or "email registration"), time (UTC), and the link
  `<admin-console-base>#/<realm>/users/<user id>/settings`. Never include a token, a code, a password, an IP
  address or the event's other details.
- Send with Keycloak's own `EmailSenderProvider` (`session.getProvider(EmailSenderProvider.class).send(
  realm.getSmtpConfig(), recipient, subject, text, null)`), so the realm's SMTP settings (section 1, the MU relay)
  apply and the provider holds no credential. Send after the transaction commits
  (`session.getTransactionManager().enlistAfterCompletion(...)`), so a failed registration sends nothing.
- A send failure is logged at WARN (realm, user id, exception class) and swallowed: registration must never fail
  because mail did.
- `onEvent(AdminEvent, boolean)`: no-op. Unit tests with a mocked session for: REGISTER sends exactly one message to
  the recipient with the fields above; LOGIN and other types send nothing; no recipient means no send; an SMTP
  exception does not propagate.
- Build against the same Keycloak version as the platform (26.8.x), Java 21; publish the jar in a small image
  pinned by digest (for example `ghcr.io/maastrichtu-ids/keycloak-registration-notice:<version>@sha256:...`).

**Deployment (platform admin):**
- In `ids3/platform/keycloak/keycloak.yaml`: an initContainer from that image copies the jar into an `emptyDir`
  mounted at `/opt/keycloak/providers` (the Keycloak image stays upstream). `start` without `--optimized` rebuilds
  with the provider at startup.
- Environment: `KC_SPI_EVENTS_LISTENER_REGISTRATION_NOTICE_RECIPIENT=<OWNER_EMAIL>`.
- Before rolling it out: `kc.sh update-compatibility check` as for any Keycloak change (`keycloak.yaml` header).
- In realm `ids-apps` only: Realm settings → Events → Event listeners → add `registration-notice` (keep
  `jboss-logging`). No other realm is affected unless its admin adds the listener.
- The `platform` namespace must reach `137.120.1.21:25` (section 1).
- Verify: register a test account; the owner receives one email with the link; a login sends nothing.

**Fallback, documented only: (b) a poller in an app namespace.** If the platform change is not wanted, a stdlib
Python loop that reads `GET /admin/realms/ids-apps/events?type=REGISTER` every few minutes with a confidential
service-account client holding `realm-management` → `view-events` and `view-users`, and mails through the relay
(an SMTP egress policy like ontoexplorer's). No platform change, but it adds a client secret (Vault, so `vault.yaml`
in that namespace), a polling delay, and state to avoid repeating or missing a notice across restarts.

Until the listener exists, the owner finds new accounts in Users, sorted by creation, or in Events filtered by
`REGISTER`.

## 7. Verify

- [ ] Register a test account by email: verification email arrives; after verifying, a token from the
      `ptm-mockups` client has `aud` containing `ptm-api` and `realm_access.roles` without `approved`.
- [ ] `GET https://ptm.k8s.semanticscience.org/api/datasets` with that token lists only public datasets and says
      the account awaits approval. Add it to `approved`, sign in again: the answer lists approved datasets (none on
      dev today).
- [ ] Link GitHub to the same account from the account console; sign in with GitHub; same user.
- [ ] An admin login asks for OTP.
- [ ] The registration notice reached the owner (once section 6 is built).
