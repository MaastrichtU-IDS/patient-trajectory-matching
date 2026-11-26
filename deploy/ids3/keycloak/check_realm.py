"""Offline check of ids-apps-realm.json against Keycloak's import format.

    python deploy/ids3/keycloak/check_realm.py [--keycloak-src DIR]

Always: the JSON parses; it holds no secret (no `secret`/`clientSecret` value, no credentials, no users); the
realm's settings agree with KEYCLOAK-REALM.md sections 1 to 5 (the values the server and the checklist depend on);
the admin-OTP flows are wired (every flow alias, execution and authenticator config they name exists) and cover
every admin-capable role, the reset-credentials flow denies those roles, and no other realm-management role but
view-* and query-* is granted.
With --keycloak-src, a directory holding Keycloak's representation sources (`<Name>.java` for Realm, Client,
IdentityProvider, Group, Role, Roles, ProtocolMapper, AuthenticationFlow, AuthenticationExecutionExport,
AbstractAuthenticationExecution, AuthenticatorConfig, RequiredActionProvider, ScopeMapping, from
core/src/main/java/org/keycloak/representations/idm/<Name>Representation.java at the platform's tag, 26.8.1, and
optionally EventType.java from server-spi-private/src/main/java/org/keycloak/events/):
every key used is a field of the representation it belongs to. This is a format check only: whether Keycloak
accepts the import is known only by importing it (KEYCLOAK-REALM.md, "Import").
"""
import argparse
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
HOST = 'https://ptm.k8s.semanticscience.org'


def fields(src, *names):
    out = set()
    for name in names:
        text = (src / f'{name}.java').read_text()
        out |= set(re.findall(r'^\s*(?:protected|private|public)\s+(?!static|final|class)[\w<>, ?\[\].]+\s+(\w+)\s*(?:=[^;]*)?;',
                              text, re.M))
        out |= set(re.findall(r'@JsonProperty\("(\w+)"\)', text))
    return out


# Every realm-management role that can grant `approved` or an app's role, change who may sign in, or act as
# another user (security review of phase 2, M2 and its re-check). Holders must use OTP in the browser flow and after
# every provider login, and are denied reset by email. Any other realm-management role a group or composite grants
# must be view-* or query-* (read only).
ADMIN_ROLES = {f'realm-management.{r}' for r in ('realm-admin', 'manage-users', 'manage-realm', 'manage-clients',
                                                 'manage-identity-providers', 'impersonation')}
READ_ONLY = re.compile(r'realm-management\.(view|query)-[a-z-]+')
AUDITED = {'REGISTER', 'LOGIN_ERROR', 'IDENTITY_PROVIDER_LINK_ACCOUNT', 'UPDATE_TOTP', 'REMOVE_TOTP', 'UPDATE_PASSWORD',
           'RESET_PASSWORD', 'IDENTITY_PROVIDER_LOGIN', 'IDENTITY_PROVIDER_FIRST_LOGIN',
           # provider and linking failures (D58: without them a failed link left no error code)
           'FEDERATED_IDENTITY_LINK', 'FEDERATED_IDENTITY_LINK_ERROR', 'IDENTITY_PROVIDER_LINK_ACCOUNT_ERROR',
           'IDENTITY_PROVIDER_LOGIN_ERROR', 'IDENTITY_PROVIDER_FIRST_LOGIN_ERROR', 'IDENTITY_PROVIDER_POST_LOGIN_ERROR',
           'IDENTITY_PROVIDER_RESPONSE_ERROR'}


def otp_roles(alias, flows, configs):
    """The roles whose holders a flow sends to the OTP form: for each conditional subflow under `alias` that holds
    `auth-otp-form`, its non-negated role conditions (a subflow with one positive condition covers that role)."""
    by_alias = {c['alias']: c['config'] for c in configs}
    covered = set()
    if alias not in flows:
        return covered
    for e in flows[alias]['authenticationExecutions']:
        if not e.get('authenticatorFlow'):
            continue
        sub = flows.get(e['flowAlias'], {'authenticationExecutions': []})['authenticationExecutions']
        if e['requirement'] == 'CONDITIONAL' and any(x.get('authenticator') == 'auth-otp-form' for x in sub):
            role = positive_role(sub, by_alias)
            if role:
                covered.add(role)
        covered |= otp_roles(e['flowAlias'], flows, configs)
    return covered


def positive_role(steps, by_alias):
    """The one non-negated role condition among a subflow's executions, or None."""
    positive = [by_alias[x['authenticatorConfig']]['condUserRole'] for x in steps
                if x.get('authenticator') == 'conditional-user-role'
                and by_alias[x['authenticatorConfig']].get('negate') != 'true']
    return positive[0] if len(positive) == 1 else None


def reset_denied(alias, flows, configs):
    """The roles whose holders the reset-credentials flow `alias` denies, and its problems: each denying subflow is
    CONDITIONAL, holds one positive role condition and `deny-access-authenticator`, and comes after the user is
    chosen and before `reset-password`; nothing in the flow resets OTP."""
    by_alias = {c['alias']: c['config'] for c in configs}
    denied, problems = set(), []
    if alias not in flows:
        return denied, ['no reset-credentials flow of our own']
    top = sorted(flows[alias]['authenticationExecutions'], key=lambda e: e['priority'])
    names = [e.get('authenticator') or e.get('flowAlias') for e in top]
    for needed in ('reset-credentials-choose-user', 'reset-password'):
        if needed not in names:
            problems.append(f'reset flow: {needed} missing')
    if problems:
        return denied, problems
    chosen, reset = names.index('reset-credentials-choose-user'), names.index('reset-password')
    for i, e in enumerate(top):
        if not e.get('authenticatorFlow'):
            continue
        sub = flows.get(e['flowAlias'], {'authenticationExecutions': []})['authenticationExecutions']
        if any(x.get('authenticator') == 'deny-access-authenticator' for x in sub):
            role = positive_role(sub, by_alias)
            if e['requirement'] == 'CONDITIONAL' and role and chosen < i < reset:
                denied.add(role)
    stack = [alias]
    while stack:
        for x in flows.get(stack.pop(), {'authenticationExecutions': []})['authenticationExecutions']:
            if x.get('authenticator') == 'reset-otp':
                problems.append('reset flow: reset-otp present (it lets a mailbox holder enrol a new OTP)')
            if x.get('authenticatorFlow'):
                stack.append(x['flowAlias'])
    return denied, problems


def granted_management_roles(realm):
    """Every realm-management role a group or a realm role's composites grant in the import."""
    out = set()
    for g in realm.get('groups', []):
        out |= {f'realm-management.{r}' for r in g.get('clientRoles', {}).get('realm-management', [])}
    for r in realm['roles'].get('realm', []):
        out |= {f'realm-management.{c}' for c in (r.get('composites') or {}).get('client', {}).get('realm-management', [])}
    return out


def check(realm, src=None):
    problems = []

    def need(cond, what):
        if not cond:
            problems.append(what)

    text = json.dumps(realm).lower()
    need('clientsecret' not in text.replace('client_secret_post', ''), 'a client secret is present')
    def keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)
    need(not {'users', 'credentials', 'secret', 'value'} & set(keys(realm)), 'users or credentials are present')
    need(realm['realm'] == 'ids-apps', 'realm name')
    for key, value in {'registrationAllowed': True, 'verifyEmail': True, 'loginWithEmailAllowed': True,
                       'duplicateEmailsAllowed': False, 'bruteForceProtected': True, 'permanentLockout': False,
                       'revokeRefreshToken': True, 'refreshTokenMaxReuse': 0, 'accessTokenLifespan': 300,
                       'eventsEnabled': True, 'adminEventsEnabled': True, 'adminEventsDetailsEnabled': False}.items():
        need(realm.get(key) == value, f'{key} should be {value!r}')
    roles = {r['name']: r for r in realm['roles']['realm']}
    # D56 (services#751): the shared realm holds broad grants only; detailed grants (full MIMIC-IV) live in ptm
    need(set(roles) == {'approved', realm['defaultRole']['name']}, 'realm roles: approved and the default role only')
    default = roles.get(realm['defaultRole']['name'], {})
    need(default.get('composites') == {'client': {'account': ['view-profile', 'manage-account']}},
         'default roles must be the account console only')
    need({'name': 'approved', 'path': '/approved', 'realmRoles': ['approved']} in realm['groups'], 'group approved')
    client = next(c for c in realm['clients'] if c['clientId'] == 'ptm-mockups')
    need(client['publicClient'] and client['standardFlowEnabled'] and not client['directAccessGrantsEnabled']
         and not client['implicitFlowEnabled'], 'ptm-mockups must be a public standard-flow client')
    need(client['redirectUris'] == [HOST + '/', HOST + '/store-query/'], 'exact redirect URIs')
    need(client['attributes']['post.logout.redirect.uris'] == f'{HOST}/##{HOST}/store-query/', 'post-logout URIs')
    need(client['webOrigins'] == [HOST], 'web origin')
    need(client['attributes']['pkce.code.challenge.method'] == 'S256', 'PKCE S256')
    need({'roles', 'basic'} <= set(client['defaultClientScopes']), 'roles and basic scopes')
    need(client.get('fullScopeAllowed') is False, 'ptm-mockups: Full scope allowed off')
    need(realm.get('scopeMappings') == [{'client': 'ptm-mockups', 'roles': ['approved']}],
         'ptm-mockups scope: realm role approved only')
    need(not realm.get('clientScopeMappings'), 'no client-role scope mappings for ptm-mockups')
    aud = [m for m in client['protocolMappers'] if m['protocolMapper'] == 'oidc-audience-mapper']
    need(aud and aud[0]['config']['included.custom.audience'] == 'ptm-api'
         and aud[0]['config']['access.token.claim'] == 'true', 'the ptm-api audience mapper on the access token')
    idps = {p['alias']: p for p in realm['identityProviders']}
    need(set(idps) == {'github', 'google', 'orcid'}, 'identity providers')
    for alias, p in idps.items():
        need(p['config']['clientId'].startswith('REPLACE_WITH_'), f'{alias}: client id must be a placeholder')
        need('clientSecret' not in p['config'], f'{alias}: no secret')
        need(p['firstBrokerLoginFlowAlias'] == 'first broker login', f'{alias}: first broker login flow')
    need(not any(p['trustEmail'] for p in idps.values()), 'no provider may trust email')
    flows = {f['alias']: f for f in realm['authenticationFlows']}
    configs = {c['alias'] for c in realm['authenticatorConfig']}
    need(realm['browserFlow'] in flows, 'the browser flow is defined')
    for f in flows.values():
        for e in f['authenticationExecutions']:
            if e.get('authenticatorFlow'):
                need(e['flowAlias'] in flows, f'flow {e["flowAlias"]} is defined')
            if 'authenticatorConfig' in e:
                need(e['authenticatorConfig'] in configs, f'authenticator config {e["authenticatorConfig"]} exists')
    # A flow succeeds only if some execution runs and succeeds (DefaultAuthenticationFlow.processFlow): a top-level
    # flow, or a REQUIRED/ALTERNATIVE subflow, made only of CONDITIONAL subflows fails for a user who matches none of
    # them, and Keycloak answers "Invalid username or password" (the post-broker flow did, live, 2026-11-26).
    must_succeed = {a for a, f in flows.items() if f.get('topLevel')}
    must_succeed |= {e['flowAlias'] for f in flows.values() for e in f['authenticationExecutions']
                     if e.get('authenticatorFlow') and e['requirement'] in ('REQUIRED', 'ALTERNATIVE')}
    for alias in sorted(must_succeed & set(flows)):
        unconditional = [e for e in flows[alias]['authenticationExecutions'] if e['requirement'] in ('REQUIRED', 'ALTERNATIVE')
                         and not str(e.get('authenticator', '')).startswith('conditional-')]
        need(unconditional, f'flow {alias}: needs a REQUIRED or ALTERNATIVE step that is not conditional, or it fails '
                            'for users who match none of its conditions')
    # OTP for everyone who can change membership or grant roles, in the browser flow and after every provider login.
    for name, flow in [('browser flow', realm['browserFlow'])] + [(f'{a}: post-broker flow', p.get('postBrokerLoginFlowAlias'))
                                                                    for a, p in idps.items()]:
        covered = otp_roles(flow, flows, realm['authenticatorConfig'])
        need(ADMIN_ROLES <= covered, f'{name}: OTP must cover {sorted(ADMIN_ROLES - covered)}')
    # No reset by email for the same roles (Keycloak's default reset flow lets a mailbox holder enrol a new OTP).
    denied, reset_problems = reset_denied(realm.get('resetCredentialsFlow'), flows, realm['authenticatorConfig'])
    problems.extend(reset_problems)
    need(ADMIN_ROLES <= denied, f'reset flow must deny {sorted(ADMIN_ROLES - denied)}')
    # Any other realm-management role granted in the import is read only.
    uncovered = {r for r in granted_management_roles(realm) if r not in ADMIN_ROLES and not READ_ONLY.fullmatch(r)}
    need(not uncovered, f'realm-management roles granted but neither read-only nor covered: {sorted(uncovered)}')
    need(not realm.get('defaultGroups'), 'no default group')
    scopes = set(client['defaultClientScopes']) | set(client.get('optionalClientScopes', []))
    need('offline_access' not in scopes, 'no offline_access scope on ptm-mockups')
    events = set(realm['enabledEventTypes'])
    need(AUDITED <= events, f'audited events missing: {sorted(AUDITED - events)}')
    actions = {a['alias']: a['enabled'] for a in realm['requiredActions']}
    need(actions.get('VERIFY_EMAIL') and actions.get('CONFIGURE_TOTP'), 'required actions')

    if src:
        def known(obj, rep, where):
            unknown = set(obj) - rep
            need(not unknown, f'{where}: keys not in Keycloak 26 representation: {sorted(unknown)}')
        known(realm, fields(src, 'Realm'), 'realm')
        known(realm['roles'], fields(src, 'Roles'), 'roles')
        for r in realm['roles']['realm']:
            known(r, fields(src, 'Role'), f'role {r["name"]}')
        known(realm['defaultRole'], fields(src, 'Role'), 'defaultRole')
        for m in realm.get('scopeMappings', []):
            known(m, fields(src, 'ScopeMapping'), f'scope mapping for {m.get("client")}')
        for g in realm['groups']:
            known(g, fields(src, 'Group'), f'group {g["name"]}')
        for c in realm['clients']:
            known(c, fields(src, 'Client'), f'client {c["clientId"]}')
            for m in c['protocolMappers']:
                known(m, fields(src, 'ProtocolMapper'), f'mapper {m["name"]}')
        for p in realm['identityProviders']:
            known(p, fields(src, 'IdentityProvider'), f'identity provider {p["alias"]}')
        for f in realm['authenticationFlows']:
            known(f, fields(src, 'AuthenticationFlow'), f'flow {f["alias"]}')
            for e in f['authenticationExecutions']:
                known(e, fields(src, 'AuthenticationExecutionExport', 'AbstractAuthenticationExecution'),
                      f'execution in {f["alias"]}')
        for c in realm['authenticatorConfig']:
            known(c, fields(src, 'AuthenticatorConfig'), f'authenticator config {c["alias"]}')
        for a in realm['requiredActions']:
            known(a, fields(src, 'RequiredActionProvider'), f'required action {a["alias"]}')
        if (src / 'EventType.java').is_file():
            event_types = set(re.findall(r'^\s+([A-Z_]+)\(', (src / 'EventType.java').read_text(), re.M))
            unknown = set(realm['enabledEventTypes']) - event_types
            need(not unknown, f'event types not in Keycloak 26: {sorted(unknown)}')
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--keycloak-src', type=Path)
    args = parser.parse_args()
    realm = json.loads((HERE / 'ids-apps-realm.json').read_text())
    problems = check(realm, args.keycloak_src)
    for p in problems:
        print('problem:', p)
    print(f'realm check: {len(problems)} problem(s)' + (' (with Keycloak 26 representation keys)' if args.keycloak_src
                                                        else ' (settings and wiring; representation keys not checked)'))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
