"""Offline check of ids-apps-realm.json against Keycloak's import format.

    python deploy/ids3/keycloak/check_realm.py [--keycloak-src DIR]

Always: the JSON parses; it holds no secret (no `secret`/`clientSecret` value, no credentials, no users); the
realm's settings agree with KEYCLOAK-REALM.md sections 1 to 5 (the values the server and the checklist depend on);
the admin-OTP flow is wired (every flow alias, execution and authenticator config it names exists).
With --keycloak-src, a directory holding Keycloak's representation sources (`<Name>.java` for Realm, Client,
IdentityProvider, Group, Role, Roles, ProtocolMapper, AuthenticationFlow, AuthenticationExecutionExport,
AbstractAuthenticationExecution, AuthenticatorConfig, RequiredActionProvider, from
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


ADMIN_ROLES = {'realm-management.manage-users', 'realm-management.manage-realm'}  # realm-admin includes both
AUDITED = {'REGISTER', 'LOGIN_ERROR', 'IDENTITY_PROVIDER_LINK_ACCOUNT', 'UPDATE_TOTP', 'REMOVE_TOTP', 'UPDATE_PASSWORD',
           'RESET_PASSWORD', 'IDENTITY_PROVIDER_LOGIN', 'IDENTITY_PROVIDER_FIRST_LOGIN'}


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
            positive = [by_alias[x['authenticatorConfig']]['condUserRole'] for x in sub
                        if x.get('authenticator') == 'conditional-user-role'
                        and by_alias[x['authenticatorConfig']].get('negate') != 'true']
            if len(positive) == 1:
                covered.add(positive[0])
        covered |= otp_roles(e['flowAlias'], flows, configs)
    return covered


def check(realm, src=None):
    problems = []

    def need(cond, what):
        if not cond:
            problems.append(what)

    text = json.dumps(realm).lower()
    need('clientsecret' not in text.replace('client_secret_post', ''), 'a client secret is present')
    need('users' not in realm and 'credentials' not in text, 'users or credentials are present')
    need(realm['realm'] == 'ids-apps', 'realm name')
    for key, value in {'registrationAllowed': True, 'verifyEmail': True, 'loginWithEmailAllowed': True,
                       'duplicateEmailsAllowed': False, 'bruteForceProtected': True, 'permanentLockout': False,
                       'revokeRefreshToken': True, 'refreshTokenMaxReuse': 0, 'accessTokenLifespan': 300,
                       'eventsEnabled': True, 'adminEventsEnabled': True, 'adminEventsDetailsEnabled': False}.items():
        need(realm.get(key) == value, f'{key} should be {value!r}')
    roles = {r['name']: r for r in realm['roles']['realm']}
    need({'approved', 'dataset:mimic-iv:read'} <= set(roles), 'realm roles approved and dataset:mimic-iv:read')
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
    # OTP for everyone who can change membership or grant roles, in the browser flow and after every provider login.
    for name, flow in [('browser flow', realm['browserFlow'])] + [(f'{a}: post-broker flow', p.get('postBrokerLoginFlowAlias'))
                                                                    for a, p in idps.items()]:
        covered = otp_roles(flow, flows, realm['authenticatorConfig'])
        need(ADMIN_ROLES <= covered, f'{name}: OTP must cover {sorted(ADMIN_ROLES - covered)}')
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
