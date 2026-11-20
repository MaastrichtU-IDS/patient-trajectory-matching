"""Who is calling, and whether they may use a dataset (docs/hosting-plan.md, section 3).

Three kinds of caller:

- **anonymous**: no `Authorization` header. Public datasets only.
- **bearer**: an OIDC access token from the realm, checked here, offline, against the realm's public keys:
  signature (RS256 only, so neither `none` nor an HMAC key can be smuggled in), `iss` equal to the configured
  issuer, `aud` containing the configured audience, `exp` and `iat` required (`nbf` checked when present) with a
  30 s leeway, `sub` required, `azp` equal to the configured client id when one is configured, and the `typ`
  claim `Bearer`, so that an ID token cannot stand in for an access token. Roles come
  from `realm_access.roles`; the `approved` realm role (granted by the realm's `approved` group) marks an account
  the owner has approved.
- **owner**: the person at a loopback-bound server with no realm configured, as before hosting existed. A server
  on a loopback address is reachable only from the machine it runs on, so it keeps serving a locally built MIMIC-IV
  demo store to its operator; the store stays content-gated to the open demo (D29). Only a request whose Host
  header is localhost, 127.0.0.1 or [::1] is the owner, so neither DNS rebinding nor a tunnel to the port gets
  owner access. `server.py` refuses to start on any other address with a non-public dataset and no realm.

The keys come from `jwks_uri`, configured separately from the issuer: on the cluster the issuer is the
browser-facing URL and the keys are fetched from the in-cluster Keycloak Service (the sulo-schema-builder pattern).
They are cached for ten minutes and fetched again, at most once every 30 seconds, when a token names an unknown key
(a rotation); one fetch at a time, outside the lock, with no redirects followed. Keys not refreshed for an hour are
no longer trusted. A present but invalid token is refused (401 TOKEN_INVALID), never treated as anonymous. Keys
that cannot be fetched refuse the request (503 AUTH_UNAVAILABLE) without affecting anonymous callers.

The in-cluster key fetch is plain HTTP, as Keycloak's Service offers no TLS inside the cluster (TLS ends at the
front door). Whoever could rewrite pod-to-pod traffic could substitute keys and forge tokens. Mitigations: the
egress NetworkPolicy lets this pod reach only Keycloak's pod on that port, the cluster is a single node so the
traffic does not leave it, redirects are refused and only RSA signing keys are taken. Where that is not enough,
PTM_OIDC_JWKS_URI can be the public https URL instead, fetched through the egress-proxy (urllib honours
HTTPS_PROXY), with TLS checked end to end (docs/hosting-plan.md, section 3).

PyJWT is imported only when a verifier is built, so local use without a realm needs nothing beyond the store lock
(`requirements-hosting.lock.txt` pins it for hosting). No token, header or claim other than the subject is ever
logged or echoed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import re
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

ALGORITHM = 'RS256'
LEEWAY = 30            # seconds of clock skew allowed on exp, nbf and iat
JWKS_TTL = 600         # seconds a fetched key set is used before it is fetched again
JWKS_MIN_REFRESH = 30  # seconds between fetches prompted by an unknown key id
JWKS_MAX_AGE = 3600    # seconds a key set is trusted without a successful fetch; past it, 503 AUTH_UNAVAILABLE
JWKS_TIMEOUT = 5
JWKS_MAX_BYTES = 64 * 1024
MAX_TOKEN = 8192       # bytes; Keycloak access tokens are a few kilobytes at most
ROLE = re.compile(r'[A-Za-z0-9][A-Za-z0-9:._-]{0,127}')


class AuthError(Exception):
    """A request refused for who is calling: an HTTP status and a code, never more detail."""

    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Principal:
    kind: str                       # 'anonymous', 'bearer' or 'owner'
    subject: str | None = None      # the token's `sub` (a pseudonymous realm id), for the request log
    roles: frozenset = field(default_factory=frozenset)
    approved: bool = False

    @property
    def signed_in(self):
        return self.kind == 'bearer'


ANONYMOUS = Principal('anonymous')
LOCAL_OWNER = Principal('owner', approved=True)


# ---------------------------------------------------------------- dataset policies

ACCESS_RANK = {'public': 0, 'approved': 1, 'role': 2}


@dataclass(frozen=True)
class Policy:
    """Who may use a dataset: `public`; `approved` (signed in, approved by the owner); or `role` (approved and
    holding `role`, e.g. dataset:mimic-iv:read)."""
    access: str
    role: str | None = None

    @classmethod
    def parse(cls, raw):
        if raw in ('public', 'approved'):
            return cls(raw)
        if isinstance(raw, dict) and set(raw) == {'role'} and isinstance(raw['role'], str) and ROLE.fullmatch(raw['role']):
            return cls('role', raw['role'])
        raise ValueError('ACCESS_MUST_BE_PUBLIC_APPROVED_OR_ROLE')

    def at_least(self, floor):
        """Whether this policy is at least as strict as `floor`: a role floor is met only by that same role."""
        if floor.access == 'role':
            return self.access == 'role' and self.role == floor.role
        return ACCESS_RANK[self.access] >= ACCESS_RANK[floor.access]

    def permits(self, principal):
        try:
            self.check(principal)
            return True
        except AuthError:
            return False

    def check(self, principal):
        """Nothing if `principal` may use the dataset; otherwise AuthError (401 LOGIN_REQUIRED, 403 NOT_APPROVED,
        403 ROLE_REQUIRED)."""
        if self.access == 'public' or principal.kind == 'owner':
            return
        if principal.kind != 'bearer':
            raise AuthError(401, 'LOGIN_REQUIRED')
        if not principal.approved:
            raise AuthError(403, 'NOT_APPROVED')
        if self.access == 'role' and self.role not in principal.roles:
            raise AuthError(403, 'ROLE_REQUIRED')

    def describe(self):
        return self.access if self.access != 'role' else {'role': self.role}


# ---------------------------------------------------------------- the realm's keys

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # the key set is read from the configured address only; a redirect is an error


def fetch_url(uri):
    """The key set's bytes: http or https only, no redirects, a short timeout, a size cap. Proxies follow the
    environment, whose NO_PROXY keeps the in-cluster address direct."""
    if urlsplit(uri).scheme not in ('http', 'https'):
        raise ValueError('JWKS_URI_MUST_BE_HTTP')
    with build_opener(_NoRedirect).open(uri, timeout=JWKS_TIMEOUT) as response:  # scheme checked above
        data = response.read(JWKS_MAX_BYTES + 1)
    if len(data) > JWKS_MAX_BYTES:
        raise ValueError('JWKS_TOO_LARGE')
    return data


class Jwks:
    """The realm's signing keys by key id: RSA signing keys for RS256 only, cached, refreshed on rotation.

    One fetch at a time, outside the lock (single flight): while it runs, other requests use the keys already held,
    or, before any key set exists, wait for it briefly. Held keys are used for at most JWKS_MAX_AGE seconds after
    their last successful fetch; if every refresh fails past that, requests get 503 AUTH_UNAVAILABLE rather than
    being judged against keys the realm may have withdrawn."""

    def __init__(self, uri, fetch=fetch_url, clock=time.monotonic, ttl=JWKS_TTL, min_refresh=JWKS_MIN_REFRESH,
                 max_age=JWKS_MAX_AGE):
        import jwt
        self._jwt = jwt
        self.uri, self._fetch, self._clock = uri, fetch, clock
        self.ttl, self.min_refresh, self.max_age = ttl, min_refresh, max_age
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._done.set()
        self._keys = {}
        self._fetched = None     # when the current keys were fetched
        self._attempted = None   # when a fetch was last started, successful or not

    def _load(self):
        """The key set from the realm, or None if it could not be read: no lock is held while this runs."""
        try:
            document = json.loads(self._fetch(self.uri))
            entries = document['keys']
            if not isinstance(entries, list):
                raise ValueError
        except Exception:  # noqa: BLE001 - unreachable, malformed or oversized: keep what we had
            return None
        keys = {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get('kty') != 'RSA' or not isinstance(entry.get('kid'), str):
                continue
            if entry.get('use', 'sig') != 'sig' or entry.get('alg', ALGORITHM) != ALGORITHM:
                continue  # an encryption key, or a key for another algorithm, never verifies a token here
            try:
                keys[entry['kid']] = self._jwt.PyJWK(entry, algorithm=ALGORITHM).key
            except Exception:  # noqa: BLE001 - a malformed key is skipped, not trusted
                continue
        return keys

    def key(self, kid):
        """The key for `kid`; None if the realm has no such key; AuthError 503 if no trustworthy key set is held.
        A fetch starts when the keys are older than the TTL or `kid` is unknown, at most once per `min_refresh`
        seconds, and never while another runs."""
        with self._lock:
            now = self._clock()
            stale = self._fetched is None or now - self._fetched >= self.ttl
            may_fetch = (self._done.is_set() and
                         (self._attempted is None or now - self._attempted >= self.min_refresh))
            fetching = (stale or kid not in self._keys) and may_fetch
            if fetching:
                self._attempted = now
                self._done.clear()
        if fetching:
            keys = None
            try:
                keys = self._load()
            finally:
                with self._lock:
                    if keys is not None:
                        self._keys, self._fetched = keys, self._attempted
                    self._done.set()
        elif self._fetched is None:
            self._done.wait(JWKS_TIMEOUT + 1)  # the first fetch is under way elsewhere
        with self._lock:
            if self._fetched is None or self._clock() - self._fetched >= self.max_age:
                raise AuthError(503, 'AUTH_UNAVAILABLE')
            return self._keys.get(kid)


# ---------------------------------------------------------------- the token check

class OidcVerifier:
    """Checks a realm access token and turns it into a Principal."""

    def __init__(self, issuer, jwks_uri, audience, client_id=None, approved_role='approved', fetch=fetch_url,
                 clock=time.monotonic):
        import jwt
        self._jwt = jwt
        if not all(isinstance(v, str) and v for v in (issuer, jwks_uri, audience)):
            raise ValueError('OIDC_CONFIG_INCOMPLETE')
        if urlsplit(jwks_uri).scheme not in ('http', 'https'):
            raise ValueError('JWKS_URI_MUST_BE_HTTP')
        self.issuer, self.audience, self.client_id = issuer, audience, client_id
        self.approved_role = approved_role or 'approved'
        self.jwks = Jwks(jwks_uri, fetch=fetch, clock=clock)

    def principal(self, token):
        jwt = self._jwt
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN:
            raise AuthError(401, 'TOKEN_INVALID')
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthError(401, 'TOKEN_INVALID') from None
        kid = header.get('kid')
        if header.get('alg') != ALGORITHM or not isinstance(kid, str):
            raise AuthError(401, 'TOKEN_INVALID')
        key = self.jwks.key(kid)
        if key is None:
            raise AuthError(401, 'TOKEN_INVALID')
        options = {'require': ['exp', 'iat', 'iss', 'aud', 'sub'], 'verify_signature': True}
        try:
            claims = jwt.decode(token, key, algorithms=[ALGORITHM], audience=self.audience, issuer=self.issuer,
                                leeway=LEEWAY, options=options)
        except jwt.PyJWTError:
            raise AuthError(401, 'TOKEN_INVALID') from None
        if claims.get('typ') != 'Bearer':
            raise AuthError(401, 'TOKEN_INVALID')  # an ID or refresh token is not an access token
        if self.client_id is not None and claims.get('azp') != self.client_id:
            raise AuthError(401, 'TOKEN_INVALID')  # issued to another client of the realm
        subject = claims.get('sub')
        if not isinstance(subject, str) or not subject:
            raise AuthError(401, 'TOKEN_INVALID')
        realm_access = claims.get('realm_access')
        raw = realm_access.get('roles') if isinstance(realm_access, dict) else None
        roles = frozenset(r for r in raw if isinstance(r, str)) if isinstance(raw, list) else frozenset()
        return Principal('bearer', subject, roles, approved=self.approved_role in roles)

    def public_config(self):
        """What the page needs to sign in: public facts, served to anyone."""
        return {'enabled': True, 'issuer': self.issuer, 'client_id': self.client_id, 'audience': self.audience}


def verifier_from_env(env):
    """An OidcVerifier from PTM_OIDC_ISSUER, PTM_OIDC_JWKS_URI, PTM_OIDC_AUDIENCE (all three, or none),
    PTM_OIDC_CLIENT_ID and PTM_APPROVED_ROLE; None when none is set."""
    names = ('PTM_OIDC_ISSUER', 'PTM_OIDC_JWKS_URI', 'PTM_OIDC_AUDIENCE')
    values = [env.get(n) or None for n in names]
    if not any(values):
        return None
    if not all(values):
        raise ValueError('OIDC_CONFIG_INCOMPLETE')
    return OidcVerifier(*values, client_id=env.get('PTM_OIDC_CLIENT_ID') or None,
                        approved_role=env.get('PTM_APPROVED_ROLE') or 'approved')


# ---------------------------------------------------------------- the request's caller

def is_loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


LOCAL_HOST = re.compile(r'(localhost|127\.0\.0\.1|\[::1\])(:[0-9]{1,5})?')


class Auth:
    """Turns a request's headers into a Principal: a verified bearer token; else the local owner on a loopback-bound
    server with no realm, for a request whose Host header is localhost, 127.0.0.1 or [::1] (so a page on another
    name, rebinding its DNS to 127.0.0.1, or a tunnel forwarding to the port, is anonymous); else anonymous."""

    def __init__(self, verifier=None, local_owner=False):
        self.verifier = verifier
        self.local_owner = local_owner and verifier is None

    def principal(self, headers):
        values = headers.get_all('Authorization') if hasattr(headers, 'get_all') else None
        header = headers.get('Authorization')
        if values is not None and len(values) > 1:
            raise AuthError(401, 'TOKEN_INVALID')
        if header is None:
            if self.local_owner and LOCAL_HOST.fullmatch(headers.get('Host') or ''):
                return LOCAL_OWNER
            return ANONYMOUS  # a request addressed to any other name (DNS rebinding, a tunnel) is not the owner
        scheme, _, token = header.strip().partition(' ')
        if scheme.lower() != 'bearer' or not token.strip():
            raise AuthError(401, 'TOKEN_INVALID')
        if self.verifier is None:
            raise AuthError(401, 'AUTH_NOT_CONFIGURED')
        return self.verifier.principal(token.strip())

    def public_config(self):
        return self.verifier.public_config() if self.verifier else {'enabled': False}
