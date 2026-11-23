"""The ids-apps realm as a Keycloak 26 partial import: the source of `ids-apps-realm.json`, matching
KEYCLOAK-REALM.md sections 1 to 5. No secret of any kind: provider client ids are placeholders, provider secrets are
entered in the admin console after import.

    python deploy/ids3/keycloak/gen_realm.py          # write ids-apps-realm.json
    python deploy/ids3/keycloak/gen_realm.py --check  # exit 1 unless the committed file is exactly what this writes

Edit this file, not the JSON, then re-generate; `test_datasets` fails if the two differ.
"""
import json
from pathlib import Path
import sys

HOST = 'https://ptm.k8s.semanticscience.org'
PAGES = [HOST + '/', HOST + '/store-query/']
DAY = 86400

# Admin-capable roles (security review of phase 2, M2, and its re-check): every realm-management role that can grant
# `approved` or a `dataset:*` role, change who may sign in, or act as another user. realm-admin includes them all;
# it is listed so that the checks name it. Holders must use OTP at every login, by password or through a provider,
# and cannot reset their credentials by email (another admin resets them in the console).
ADMIN_ROLES = ['realm-admin', 'manage-users', 'manage-realm', 'manage-clients', 'manage-identity-providers',
               'impersonation']


def execution(authenticator=None, flow=None, requirement='REQUIRED', priority=10, config=None):
    e = {'requirement': requirement, 'priority': priority, 'authenticatorFlow': flow is not None,
         'autheticatorFlow': flow is not None, 'userSetupAllowed': False}
    e.update({'flowAlias': flow} if flow else {'authenticator': authenticator})
    if config:
        e['authenticatorConfig'] = config
    return e


CONFIGS = [{'alias': f'holds {r}', 'config': {'condUserRole': f'realm-management.{r}', 'negate': 'false'}}
           for r in ADMIN_ROLES]
CONFIGS += [{'alias': f'does not hold {r}', 'config': {'condUserRole': f'realm-management.{r}', 'negate': 'true'}}
            for r in ADMIN_ROLES[:-1]]
CONFIGS += [{'alias': 'admins reset through another admin',
             'config': {'denyErrorMessage': 'Administrators cannot reset their credentials by email. Ask another '
                                            'administrator to reset them in the admin console.'}}]


def otp_subflows(prefix):
    """One CONDITIONAL subflow per admin role, each ending in the OTP form. Conditions in a subflow are ANDed, so
    subflow k asks holders of role k who hold none of the roles before it: an admin is asked exactly once."""
    flows, executions = [], []
    for k, role in enumerate(ADMIN_ROLES):
        alias = f'{prefix} OTP: {role}'
        steps = [execution('conditional-user-role', priority=10, config=f'holds {role}')]
        steps += [execution('conditional-user-role', priority=20 + j, config=f'does not hold {earlier}')
                  for j, earlier in enumerate(ADMIN_ROLES[:k])]
        steps.append(execution('auth-otp-form', priority=90))
        flows.append({'alias': alias, 'description': f'OTP for holders of realm-management/{role}'
                      + (' who hold none of the roles before it.' if k else '.'),
                      'providerId': 'basic-flow', 'topLevel': False, 'builtIn': False,
                      'authenticationExecutions': steps})
        executions.append(execution(flow=alias, requirement='CONDITIONAL', priority=20 + 10 * k))
    return flows, executions


browser_otp, browser_steps = otp_subflows('browser')
broker_otp, broker_steps = otp_subflows('post broker')
deny = []
for k, role in enumerate(ADMIN_ROLES):
    deny.append({'alias': f'reset denied: {role}', 'description': f'No reset by email for holders of '
                 f'realm-management/{role}.', 'providerId': 'basic-flow', 'topLevel': False, 'builtIn': False,
                 'authenticationExecutions': [
                     execution('conditional-user-role', priority=10, config=f'holds {role}'),
                     execution('deny-access-authenticator', priority=20,
                               config='admins reset through another admin')]})
FLOWS = [
    {'alias': 'browser with admin OTP', 'description': 'Browser login; OTP for admin-capable users.',
     'providerId': 'basic-flow', 'topLevel': True, 'builtIn': False,
     'authenticationExecutions': [
         execution('auth-cookie', requirement='ALTERNATIVE', priority=10),
         execution('identity-provider-redirector', requirement='ALTERNATIVE', priority=25),
         execution(flow='admin OTP forms', requirement='ALTERNATIVE', priority=30)]},
    {'alias': 'admin OTP forms', 'description': 'Username and password, then OTP for admin-capable users.',
     'providerId': 'basic-flow', 'topLevel': False, 'builtIn': False,
     'authenticationExecutions': [execution('auth-username-password-form', priority=10)] + browser_steps},
    *browser_otp,
    {'alias': 'post broker admin OTP', 'description': 'After an identity-provider login: OTP for admin-capable users.',
     'providerId': 'basic-flow', 'topLevel': True, 'builtIn': False, 'authenticationExecutions': broker_steps},
    *broker_otp,
    # Keycloak's default reset flow ends in "Reset - Conditional OTP", whose reset-otp only adds CONFIGURE_TOTP: so
    # whoever held an admin's mailbox could set a password, enrol their own OTP and get in. Here the link is still
    # mailed (so the form says nothing about who is an admin), but following it as an admin is denied before any
    # credential changes; nobody resets OTP by email.
    {'alias': 'reset credentials, not for admins', 'description': 'Password reset by email for ordinary users only.',
     'providerId': 'basic-flow', 'topLevel': True, 'builtIn': False,
     'authenticationExecutions': [
         execution('reset-credentials-choose-user', priority=10),
         execution('reset-credential-email', priority=20),
         *[execution(flow=d['alias'], requirement='CONDITIONAL', priority=30 + k) for k, d in enumerate(deny)],
         execution('reset-password', priority=90)]},
    *deny,
]

realm = {
    'realm': 'ids-apps',
    'enabled': True,
    'displayName': 'Semantic Science apps',
    'sslRequired': 'external',
    # 1. Login and registration
    'registrationAllowed': True,
    'registrationEmailAsUsername': False,
    'rememberMe': True,
    'verifyEmail': True,
    'loginWithEmailAllowed': True,
    'duplicateEmailsAllowed': False,
    'resetPasswordAllowed': True,
    'editUsernameAllowed': False,
    # Brute-force detection: temporary lockout
    'bruteForceProtected': True,
    'permanentLockout': False,
    'failureFactor': 10,
    'waitIncrementSeconds': 60,
    'quickLoginCheckMilliSeconds': 1000,
    'minimumQuickLoginWaitSeconds': 60,
    'maxFailureWaitSeconds': 900,
    'maxDeltaTimeSeconds': 43200,
    'passwordPolicy': 'length(12) and notUsername and notEmail and passwordHistory(3)',
    # Sessions and tokens
    'accessTokenLifespan': 300,
    'ssoSessionIdleTimeout': 1800,
    'ssoSessionMaxLifespan': 36000,
    'offlineSessionIdleTimeout': 30 * DAY,
    'offlineSessionMaxLifespanEnabled': False,
    'revokeRefreshToken': True,       # refresh-token rotation
    'refreshTokenMaxReuse': 0,        # each refresh token works once
    # Email through the MU relay (no authentication, no TLS on :25)
    'smtpServer': {'host': 'smtp.maastrichtuniversity.nl', 'port': '25', 'from': 'info-ids@maastrichtuniversity.nl',
                   'fromDisplayName': 'Semantic Science apps', 'auth': 'false', 'ssl': 'false', 'starttls': 'false'},
    # Events: user events 90 days, admin events one year, no representations
    'eventsEnabled': True,
    'eventsExpiration': 90 * DAY,
    'enabledEventTypes': ['REGISTER', 'LOGIN', 'LOGIN_ERROR', 'UPDATE_EMAIL', 'VERIFY_EMAIL',
                          'IDENTITY_PROVIDER_LINK_ACCOUNT', 'REMOVE_FEDERATED_IDENTITY', 'DELETE_ACCOUNT',
                          # admin forensics (security review of phase 2, L4)
                          'UPDATE_TOTP', 'REMOVE_TOTP', 'UPDATE_CREDENTIAL', 'REMOVE_CREDENTIAL', 'UPDATE_PASSWORD',
                          'RESET_PASSWORD', 'SEND_RESET_PASSWORD', 'IDENTITY_PROVIDER_LOGIN',
                          'IDENTITY_PROVIDER_FIRST_LOGIN'],
    'eventsListeners': ['jboss-logging'],
    'adminEventsEnabled': True,
    'adminEventsDetailsEnabled': False,
    'attributes': {'adminEventsExpiration': str(365 * DAY)},
    # 2. Roles and groups: new accounts get nothing but the account console
    'roles': {
        'realm': [
            {'name': 'approved', 'description': 'Approved by the owner; required by every non-public dataset.'},
            {'name': 'dataset:mimic-iv:read',
             'description': "Full MIMIC-IV: granted only after the owner has verified and recorded the user's own "
                            'PhysioNet credentialing. Assigned to nobody until the storage and backup access review.'},
            {'name': 'default-roles-ids-apps', 'description': 'The roles every new account gets: the account console only.',
             'composite': True, 'composites': {'client': {'account': ['view-profile', 'manage-account']}}},
        ],
    },
    'defaultRole': {'name': 'default-roles-ids-apps', 'composite': True, 'clientRole': False},
    'groups': [{'name': 'approved', 'path': '/approved', 'realmRoles': ['approved']}],
    # 3. The ptm client
    'clients': [{
        'clientId': 'ptm-mockups',
        'name': 'Patient trajectory matching (mockups)',
        'enabled': True,
        'protocol': 'openid-connect',
        'publicClient': True,
        'standardFlowEnabled': True,
        'implicitFlowEnabled': False,
        'directAccessGrantsEnabled': False,
        'serviceAccountsEnabled': False,
        'frontchannelLogout': True,
        'fullScopeAllowed': True,
        'rootUrl': HOST,
        'baseUrl': '/',
        'redirectUris': PAGES,
        'webOrigins': [HOST],
        'attributes': {
            'pkce.code.challenge.method': 'S256',
            'post.logout.redirect.uris': '##'.join(PAGES),
            'oauth2.device.authorization.grant.enabled': 'false',
            'client.use.lightweight.access.token.enabled': 'false',
        },
        'defaultClientScopes': ['web-origins', 'acr', 'profile', 'roles', 'basic', 'email'],
        'optionalClientScopes': ['address', 'phone', 'microprofile-jwt'],  # no offline_access
        'protocolMappers': [{
            'name': 'ptm-api-audience',
            'protocol': 'openid-connect',
            'protocolMapper': 'oidc-audience-mapper',
            'consentRequired': False,
            'config': {'included.custom.audience': 'ptm-api', 'access.token.claim': 'true',
                       'id.token.claim': 'false', 'introspection.token.claim': 'true',
                       'lightweight.claim': 'false'},
        }],
    }],
    # 4. Identity providers: placeholder client ids, NO secrets (entered in the admin console after import)
    'identityProviders': [
        {'alias': 'github', 'displayName': 'GitHub', 'providerId': 'github', 'enabled': True, 'trustEmail': False,
         'storeToken': False, 'addReadTokenRoleOnCreate': False, 'linkOnly': False,
         'firstBrokerLoginFlowAlias': 'first broker login', 'postBrokerLoginFlowAlias': 'post broker admin OTP',
         'config': {'clientId': 'REPLACE_WITH_GITHUB_CLIENT_ID', 'defaultScope': 'user:email', 'syncMode': 'IMPORT',
                    'guiOrder': '1'}},
        {'alias': 'google', 'displayName': 'Google', 'providerId': 'google', 'enabled': True, 'trustEmail': False,
         'storeToken': False, 'addReadTokenRoleOnCreate': False, 'linkOnly': False,
         'firstBrokerLoginFlowAlias': 'first broker login', 'postBrokerLoginFlowAlias': 'post broker admin OTP',
         'config': {'clientId': 'REPLACE_WITH_GOOGLE_CLIENT_ID', 'syncMode': 'IMPORT', 'guiOrder': '2'}},
        {'alias': 'orcid', 'displayName': 'ORCID', 'providerId': 'oidc', 'enabled': True, 'trustEmail': False,
         'storeToken': False, 'addReadTokenRoleOnCreate': False, 'linkOnly': False,
         'firstBrokerLoginFlowAlias': 'first broker login', 'postBrokerLoginFlowAlias': 'post broker admin OTP',
         'config': {'clientId': 'REPLACE_WITH_ORCID_CLIENT_ID', 'issuer': 'https://orcid.org',
                    'authorizationUrl': 'https://orcid.org/oauth/authorize', 'tokenUrl': 'https://orcid.org/oauth/token',
                    'userInfoUrl': 'https://orcid.org/oauth/userinfo', 'jwksUrl': 'https://orcid.org/oauth/jwks',
                    'useJwksUrl': 'true', 'validateSignature': 'true', 'clientAuthMethod': 'client_secret_post',
                    'defaultScope': 'openid', 'syncMode': 'IMPORT', 'pkceEnabled': 'false', 'guiOrder': '3'}},
    ],
    # 5. MFA for admins: OTP required for holders of the admin-capable roles (ADMIN_ROLES), nobody else
    'otpPolicyType': 'totp',
    'otpPolicyAlgorithm': 'HmacSHA1',
    'otpPolicyDigits': 6,
    'otpPolicyPeriod': 30,
    'otpPolicyLookAheadWindow': 1,
    'browserFlow': 'browser with admin OTP',
    'resetCredentialsFlow': 'reset credentials, not for admins',
    'authenticatorConfig': CONFIGS,
    'authenticationFlows': FLOWS,
    'requiredActions': [
        {'alias': 'VERIFY_EMAIL', 'name': 'Verify Email', 'providerId': 'VERIFY_EMAIL', 'enabled': True,
         'defaultAction': False, 'priority': 10, 'config': {}},
        {'alias': 'CONFIGURE_TOTP', 'name': 'Configure OTP', 'providerId': 'CONFIGURE_TOTP', 'enabled': True,
         'defaultAction': False, 'priority': 20, 'config': {}},
        {'alias': 'UPDATE_PASSWORD', 'name': 'Update Password', 'providerId': 'UPDATE_PASSWORD', 'enabled': True,
         'defaultAction': False, 'priority': 30, 'config': {}},
        {'alias': 'UPDATE_PROFILE', 'name': 'Update Profile', 'providerId': 'UPDATE_PROFILE', 'enabled': True,
         'defaultAction': False, 'priority': 40, 'config': {}},
        {'alias': 'TERMS_AND_CONDITIONS', 'name': 'Terms and Conditions', 'providerId': 'TERMS_AND_CONDITIONS',
         'enabled': False, 'defaultAction': False, 'priority': 50, 'config': {}},
        {'alias': 'webauthn-register', 'name': 'Webauthn Register', 'providerId': 'webauthn-register',
         'enabled': True, 'defaultAction': False, 'priority': 60, 'config': {}},
    ],
}
OUT = Path(__file__).resolve().parent / 'ids-apps-realm.json'


def render():
    """The realm file's exact text."""
    return json.dumps(realm, indent=2) + '\n'


def main(argv):
    if argv == ['--check']:
        same = OUT.is_file() and OUT.read_text() == render()
        print('realm file: ' + ('matches the generator' if same else 'DIFFERS from the generator; re-generate'))
        return 0 if same else 1
    if argv:
        print(__doc__)
        return 2
    OUT.write_text(render())
    print(OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
