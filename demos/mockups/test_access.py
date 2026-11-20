"""The bearer-token check and the dataset policies (access.py), against keys generated here: no network, no realm."""
import base64
import json
from pathlib import Path
import socketserver
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from email.message import Message

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import access  # noqa: E402

import jwt  # noqa: E402 - requirements-hosting.lock.txt
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402

ISSUER = 'https://keycloak.example.test/realms/ids-apps'
JWKS_URI = 'http://keycloak.platform.svc.cluster.local:8080/realms/ids-apps/protocol/openid-connect/certs'
AUDIENCE = 'ptm-api'


class Realm:
    """A stand-in realm: RSA signing keys, a key set, and tokens shaped like Keycloak's access tokens."""

    def __init__(self):
        self.keys = {'k1': rsa.generate_private_key(public_exponent=65537, key_size=2048)}
        self.fetches = 0
        self.down = False

    def add_key(self, kid):
        self.keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(self, extra=()):
        keys = []
        for kid, key in self.keys.items():
            jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
            keys.append({**jwk, 'kid': kid, 'use': 'sig', 'alg': 'RS256'})
        return {'keys': keys + list(extra)}

    def fetch(self, uri):
        self.fetches += 1
        if self.down:
            raise OSError('connection refused')
        assert uri == JWKS_URI
        return json.dumps(self.jwks()).encode()

    def claims(self, **changes):
        now = int(time.time())
        claims = {'iss': ISSUER, 'aud': [AUDIENCE, 'account'], 'sub': 'f3c1e8a0-user', 'typ': 'Bearer',
                  'azp': 'ptm-mockups', 'iat': now, 'exp': now + 300,
                  'realm_access': {'roles': ['approved', 'dataset:mimic-iv:read']}}
        claims.update(changes)
        return {k: v for k, v in claims.items() if v is not None}

    def token(self, kid='k1', key=None, headers=None, **changes):
        return jwt.encode(self.claims(**changes), key or self.keys[kid], algorithm='RS256',
                          headers={'kid': kid, **(headers or {})})


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.realm = Realm()
        self.clock = [1000.0]
        self.verifier = access.OidcVerifier(ISSUER, JWKS_URI, AUDIENCE, client_id='ptm-mockups',
                                            fetch=self.realm.fetch, clock=lambda: self.clock[0])

    def refused(self, token, status=401, code='TOKEN_INVALID'):
        with self.assertRaises(access.AuthError) as caught:
            self.verifier.principal(token)
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))

    def test_a_valid_access_token_gives_its_subject_and_roles(self):
        principal = self.verifier.principal(self.realm.token())
        self.assertEqual(principal.kind, 'bearer')
        self.assertEqual(principal.subject, 'f3c1e8a0-user')
        self.assertTrue(principal.approved)
        self.assertEqual(principal.roles, {'approved', 'dataset:mimic-iv:read'})
        self.assertTrue(principal.signed_in)

    def test_a_single_audience_string_is_accepted(self):
        self.assertTrue(self.verifier.principal(self.realm.token(aud=AUDIENCE)).approved)

    def test_an_account_without_the_approved_role_is_signed_in_but_not_approved(self):
        principal = self.verifier.principal(self.realm.token(realm_access={'roles': ['default-roles-ids-apps']}))
        self.assertEqual((principal.kind, principal.approved), ('bearer', False))
        principal = self.verifier.principal(self.realm.token(realm_access=None))
        self.assertEqual((principal.roles, principal.approved), (frozenset(), False))
        principal = self.verifier.principal(self.realm.token(realm_access={'roles': 'approved'}))  # not a list
        self.assertFalse(principal.approved)

    def test_expired_tokens_are_refused(self):
        now = int(time.time())
        self.refused(self.realm.token(iat=now - 4000, exp=now - 3600))
        self.refused(self.realm.token(iat=now - 400, exp=now - access.LEEWAY - 5))
        # inside the leeway still passes
        self.verifier.principal(self.realm.token(iat=now - 400, exp=now - 5))

    def test_tokens_from_another_issuer_are_refused(self):
        self.refused(self.realm.token(iss='https://keycloak.example.test/realms/ids3'))
        self.refused(self.realm.token(iss=ISSUER + '/'))
        self.refused(self.realm.token(iss=None))

    def test_tokens_for_another_audience_are_refused(self):
        self.refused(self.realm.token(aud='account'))
        self.refused(self.realm.token(aud=['other-api']))
        self.refused(self.realm.token(aud=None))

    def test_required_time_claims_and_future_tokens(self):
        now = int(time.time())
        self.refused(self.realm.token(exp=None))
        self.refused(self.realm.token(iat=None))
        self.refused(self.realm.token(nbf=now + 600))
        self.refused(self.realm.token(iat=now + 600, exp=now + 900))

    def test_an_id_token_or_refresh_token_is_not_an_access_token(self):
        self.refused(self.realm.token(typ='ID'))
        self.refused(self.realm.token(typ='Refresh'))
        self.refused(self.realm.token(typ=None))

    def test_tampered_tokens_are_refused(self):
        token = self.realm.token(realm_access={'roles': []})
        header, payload, signature = token.split('.')
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        claims['realm_access'] = {'roles': ['approved', 'dataset:mimic-iv:read']}
        forged = '.'.join([header, b64(json.dumps(claims).encode()), signature])
        self.refused(forged)
        flipped = signature[:-2] + ('A' if signature[-2] != 'A' else 'B') + signature[-1]
        self.refused('.'.join([header, payload, flipped]))
        self.refused('.'.join([header, payload, '']))
        self.refused('.'.join([header, payload]))
        self.refused('not a token')
        self.refused('')
        self.refused('a' * (access.MAX_TOKEN + 1))

    def test_a_token_signed_by_a_key_the_realm_does_not_hold_is_refused(self):
        stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.refused(self.realm.token(key=stranger))            # under a known key id
        self.refused(self.realm.token(kid='k9', key=stranger))   # under an unknown one

    def test_algorithm_confusion_is_refused(self):
        claims = self.realm.claims()
        unsigned = '.'.join([b64(json.dumps({'alg': 'none', 'kid': 'k1', 'typ': 'JWT'}).encode()),
                             b64(json.dumps(claims).encode()), ''])
        self.refused(unsigned)
        # HS256 keyed with the realm's public key, the classic confusion: never accepted, whatever the key bytes.
        public_pem = self.realm.keys['k1'].public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        header = b64(json.dumps({'alg': 'HS256', 'kid': 'k1', 'typ': 'JWT'}).encode())
        body = b64(json.dumps(claims).encode())
        import hashlib
        import hmac
        mac = b64(hmac.new(public_pem, f'{header}.{body}'.encode(), hashlib.sha256).digest())
        self.refused(f'{header}.{body}.{mac}')
        # RS512 or PS256 with the right key: still refused, RS256 only.
        self.refused(jwt.encode(claims, self.realm.keys['k1'], algorithm='RS512', headers={'kid': 'k1'}))
        self.refused(jwt.encode(claims, self.realm.keys['k1'], algorithm='PS256', headers={'kid': 'k1'}))
        # a token without a key id
        self.refused(jwt.encode(claims, self.realm.keys['k1'], algorithm='RS256'))

    def test_keys_are_cached_and_refetched_on_rotation_at_most_every_30_seconds(self):
        self.verifier.principal(self.realm.token())
        self.verifier.principal(self.realm.token())
        self.assertEqual(self.realm.fetches, 1)
        self.realm.add_key('k2')                     # the realm rotates in a new key
        self.clock[0] += access.JWKS_MIN_REFRESH + 1
        self.verifier.principal(self.realm.token(kid='k2'))
        self.assertEqual(self.realm.fetches, 2)
        for _ in range(5):                           # unknown key ids cannot force a fetch per request
            self.refused(self.realm.token(kid='nope', key=self.realm.keys['k1']))
        self.assertEqual(self.realm.fetches, 2)
        self.clock[0] += access.JWKS_TTL + 1         # stale keys are fetched again
        self.verifier.principal(self.realm.token())
        self.assertEqual(self.realm.fetches, 3)

    def test_an_unreachable_realm_is_named_and_held_keys_stay_in_use(self):
        self.realm.down = True
        self.refused(self.realm.token(), 503, 'AUTH_UNAVAILABLE')
        self.realm.down = False
        self.refused(self.realm.token(), 503, 'AUTH_UNAVAILABLE')  # no fetch again within 30 s
        self.clock[0] += access.JWKS_MIN_REFRESH + 1
        self.verifier.principal(self.realm.token())
        self.realm.down = True
        self.clock[0] += access.JWKS_TTL + 1
        self.verifier.principal(self.realm.token())  # the outage keeps the keys already held

    def test_only_rsa_signing_keys_for_rs256_are_taken_from_the_key_set(self):
        ec_key = ec.generate_private_key(ec.SECP256R1())
        ec_jwk = {**jwt.algorithms.ECAlgorithm.to_jwk(ec_key.public_key(), as_dict=True), 'kid': 'e1', 'alg': 'ES256'}
        realm = self.realm
        enc = {**realm.jwks()['keys'][0], 'kid': 'enc', 'use': 'enc', 'alg': 'RSA-OAEP'}
        rs512 = {**realm.jwks()['keys'][0], 'kid': 'k512', 'alg': 'RS512'}
        broken = {'kty': 'RSA', 'kid': 'broken', 'n': '!!', 'e': 'AQAB'}
        document = realm.jwks(extra=[ec_jwk, enc, rs512, broken, 'junk', {'kid': 7, 'kty': 'RSA'}])
        jwks = access.Jwks(JWKS_URI, fetch=lambda uri: json.dumps(document).encode())
        self.assertIsNotNone(jwks.key('k1'))
        for kid in ('e1', 'enc', 'k512', 'broken'):
            self.assertIsNone(jwks.key(kid), kid)
        bad = access.Jwks(JWKS_URI, fetch=lambda uri: b'{"keys": "nope"}')
        with self.assertRaises(access.AuthError):
            bad.key('k1')

    def test_configuration_is_all_three_or_none(self):
        self.assertIsNone(access.verifier_from_env({}))
        env = {'PTM_OIDC_ISSUER': ISSUER, 'PTM_OIDC_JWKS_URI': JWKS_URI, 'PTM_OIDC_AUDIENCE': AUDIENCE}
        verifier = access.verifier_from_env({**env, 'PTM_OIDC_CLIENT_ID': 'ptm-mockups'})
        self.assertEqual(verifier.public_config(),
                         {'enabled': True, 'issuer': ISSUER, 'client_id': 'ptm-mockups', 'audience': AUDIENCE})
        for missing in env:
            with self.assertRaises(ValueError):
                access.verifier_from_env({k: v for k, v in env.items() if k != missing})
        with self.assertRaises(ValueError):
            access.OidcVerifier(ISSUER, 'file:///etc/passwd', AUDIENCE)


class FetchTests(unittest.TestCase):
    """The real fetch, against a key set served on loopback by this test."""

    def test_the_fetch_reads_a_small_key_set_and_refuses_a_large_one_or_another_scheme(self):
        payload = {'small': b'{"keys": []}', 'large': b' ' * (access.JWKS_MAX_BYTES + 10)}

        class Keys(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/moved':  # a redirect is not followed
                    self.send_response(302)
                    self.send_header('Location', '/small')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                body = payload[self.path.strip('/')]
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        class Quick(HTTPServer):
            def server_bind(self):  # skip http.server's reverse-DNS lookup, slow on some machines
                socketserver.TCPServer.server_bind(self)
        httpd = Quick(('127.0.0.1', 0), Keys)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f'http://127.0.0.1:{httpd.server_address[1]}/'
            self.assertEqual(access.fetch_url(base + 'small'), b'{"keys": []}')
            with self.assertRaises(ValueError):
                access.fetch_url(base + 'large')
            with self.assertRaises(ValueError):
                access.fetch_url('file:///etc/hosts')
            with self.assertRaises(OSError):  # HTTPError for the unfollowed 302
                access.fetch_url(base + 'moved')
        finally:
            httpd.shutdown()
            httpd.server_close()


class CallerTests(unittest.TestCase):
    def headers(self, *values):
        message = Message()
        for value in values:
            message['Authorization'] = value
        return message

    def test_the_caller_from_the_headers(self):
        realm = Realm()
        verifier = access.OidcVerifier(ISSUER, JWKS_URI, AUDIENCE, fetch=realm.fetch)
        with_realm = access.Auth(verifier, local_owner=True)
        self.assertFalse(with_realm.local_owner, 'a configured realm turns the loopback owner off')
        self.assertIs(with_realm.principal(self.headers()), access.ANONYMOUS)
        self.assertTrue(with_realm.principal(self.headers('Bearer ' + realm.token())).approved)
        for bad in ('Basic b3duZXI6c2VjcmV0', 'Bearer', 'Bearer   ', 'Token abc'):
            with self.assertRaises(access.AuthError) as caught:
                with_realm.principal(self.headers(bad))
            self.assertEqual(caught.exception.code, 'TOKEN_INVALID')
        with self.assertRaises(access.AuthError):  # two Authorization headers
            with_realm.principal(self.headers('Bearer ' + realm.token(), 'Bearer ' + realm.token()))

        owner = access.Auth(None, local_owner=True)
        self.assertIs(owner.principal(self.headers()), access.LOCAL_OWNER)
        anonymous = access.Auth(None, local_owner=False)
        self.assertIs(anonymous.principal(self.headers()), access.ANONYMOUS)
        with self.assertRaises(access.AuthError) as caught:
            anonymous.principal(self.headers('Bearer ' + realm.token()))
        self.assertEqual((caught.exception.status, caught.exception.code), (401, 'AUTH_NOT_CONFIGURED'))
        self.assertEqual(anonymous.public_config(), {'enabled': False})

    def test_loopback_addresses(self):
        for host in ('127.0.0.1', '127.0.0.2', '::1', 'localhost'):
            self.assertTrue(access.is_loopback(host), host)
        for host in ('0.0.0.0', '::', '10.42.0.7', '137.120.31.148', 'example.org', ''):
            self.assertFalse(access.is_loopback(host), host)


class PolicyTests(unittest.TestCase):
    signed_in = access.Principal('bearer', 's', frozenset())
    approved = access.Principal('bearer', 's', frozenset({'approved'}), approved=True)
    credentialed = access.Principal('bearer', 's', frozenset({'approved', 'dataset:mimic-iv:read'}), approved=True)
    # A role without approval: withdrawn from the group, role left behind. The role alone does not open the dataset.
    role_only = access.Principal('bearer', 's', frozenset({'dataset:mimic-iv:read'}), approved=False)

    def outcome(self, policy, principal):
        try:
            policy.check(principal)
            return 'ok'
        except access.AuthError as error:
            return f'{error.status} {error.code}'

    def test_each_tier_for_each_caller(self):
        public, approved = access.Policy('public'), access.Policy('approved')
        role = access.Policy('role', 'dataset:mimic-iv:read')
        table = {
            access.ANONYMOUS: ('ok', '401 LOGIN_REQUIRED', '401 LOGIN_REQUIRED'),
            self.signed_in: ('ok', '403 NOT_APPROVED', '403 NOT_APPROVED'),
            self.approved: ('ok', 'ok', '403 ROLE_REQUIRED'),
            self.credentialed: ('ok', 'ok', 'ok'),
            self.role_only: ('ok', '403 NOT_APPROVED', '403 NOT_APPROVED'),
            access.LOCAL_OWNER: ('ok', 'ok', 'ok'),
        }
        for principal, expected in table.items():
            self.assertEqual(tuple(self.outcome(p, principal) for p in (public, approved, role)), expected, principal)
            self.assertEqual(role.permits(principal), expected[2] == 'ok')

    def test_parsing_and_floors(self):
        self.assertEqual(access.Policy.parse('public'), access.Policy('public'))
        self.assertEqual(access.Policy.parse({'role': 'dataset:x:read'}), access.Policy('role', 'dataset:x:read'))
        for bad in ('Public', 'role', {'role': ''}, {'role': 'a b'}, {'role': 'x', 'extra': 1}, None, 3):
            with self.assertRaises(ValueError):
                access.Policy.parse(bad)
        public, approved = access.Policy('public'), access.Policy('approved')
        role, other = access.Policy('role', 'dataset:mimic-iv:read'), access.Policy('role', 'dataset:other:read')
        self.assertTrue(approved.at_least(public) and role.at_least(approved) and role.at_least(role))
        self.assertFalse(public.at_least(approved) or approved.at_least(role) or other.at_least(role))


if __name__ == '__main__':
    unittest.main()
