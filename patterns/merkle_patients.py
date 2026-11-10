"""A Merkle tree over patients, for the execution store's input digest (D16; D27).

Not wired in: by the project lead's decision (D27 draft, decision 1) the store keeps D20's flat input
digest as its identity, and no root is recorded or written by any run until incremental appends exist.

Pure functions over `(patient_id, patient_digest)` pairs. Nothing here reads the store: the
caller supplies each patient's digest (the store's own canonical-JSON sha256, or the reference's)
and, for the two-level form, the bucket function. So the store, its reference and a sharded
ingest can each compute the same root independently.

Construction (RFC 6962 / RFC 9162 section 2.1, the Certificate Transparency Merkle tree):
- A leaf is `sha256(0x00 || patient_id || TAB || patient_digest)`, the identifier as UTF-8 and the
  digest as 64 lower-case hex characters. The 0x00 and 0x01 prefixes separate leaves from inner
  nodes, so no inner node can be passed off as a leaf (second-preimage resistance).
- An inner node is `sha256(0x01 || left || right)`. A list of n > 1 leaves splits at k, the largest
  power of two below n: the left subtree holds the first k leaves. A lone last node is never
  paired with itself (the duplicated-node ambiguity of some other trees), so distinct leaf lists
  have distinct roots.
- The empty tree's root is `sha256(b'')`.
- Leaves are in code-point order of `patient_id` (Python's `str` order, which is UTF-8 byte order),
  each identifier once. So the root does not depend on ingest order, shard plan or part size.

Two-level form (`bucket_tree`): one tree per bucket over that bucket's patients, then one tree over
the bucket roots in bucket order, empty buckets included as the empty root. An append that touches
one bucket rehashes that bucket's tree and the top, and a bucket's root can be computed by whoever
holds that bucket.

`input_digest(header_bytes, pairs, ...)` binds a caller-canonicalised header (profile, dataset,
closure, provenance) to the root and the patient count. `inclusion_proof` and `verify_inclusion`
show that one patient's digest is part of a root without the other patients' digests.
"""
from __future__ import annotations

import hashlib
import re

PROFILE = 'merkle-patients-1'
LEAF_PREFIX = b'\x00'
NODE_PREFIX = b'\x01'
EMPTY_ROOT = hashlib.sha256(b'').digest()
_HEX64 = re.compile(r'[0-9a-f]{64}')


class MerkleError(ValueError):
    """An input the tree refuses; the message is a stable code."""


def _require(condition, code):
    if not condition:
        raise MerkleError(code)


def leaf_hash(patient_id, patient_digest):
    """The leaf for one patient: sha256(0x00 || id || TAB || hex digest)."""
    _require(isinstance(patient_id, str) and patient_id != '' and not any(c in patient_id for c in '\t\n\r'),
             'INVALID_PATIENT_ID')
    _require(isinstance(patient_digest, str) and bool(_HEX64.fullmatch(patient_digest)), 'INVALID_PATIENT_DIGEST')
    return hashlib.sha256(LEAF_PREFIX + patient_id.encode('utf-8') + b'\t' + patient_digest.encode('ascii')).digest()


def node_hash(left, right):
    return hashlib.sha256(NODE_PREFIX + left + right).digest()


def _split(n):
    """The largest power of two strictly below n (n >= 2)."""
    return 1 << ((n - 1).bit_length() - 1)


def root_of(leaves):
    """RFC 6962 Merkle tree hash of a list of leaf hashes (bytes)."""
    leaves = list(leaves)

    def mth(lo, hi):
        n = hi - lo
        if n == 1:
            return leaves[lo]
        k = _split(n)
        return node_hash(mth(lo, lo + k), mth(lo + k, hi))

    return mth(0, len(leaves)) if leaves else EMPTY_ROOT


class StreamingRoot:
    """The same root as `root_of`, from leaves pushed in order, holding O(log n) hashes.

    The stack holds complete subtrees of strictly decreasing size. Pushing merges equal sizes;
    the final fold from the right gives the RFC 6962 tree, whose left subtrees are always the
    largest complete power of two.
    """

    def __init__(self):
        self._stack = []  # [(size, hash)]
        self.count = 0

    def push(self, leaf):
        size, digest = 1, leaf
        while self._stack and self._stack[-1][0] == size:
            left_size, left = self._stack.pop()
            size, digest = size + left_size, node_hash(left, digest)
        self._stack.append((size, digest))
        self.count += 1

    def root(self):
        if not self._stack:
            return EMPTY_ROOT
        digest = self._stack[-1][1]
        for _, left in reversed(self._stack[:-1]):
            digest = node_hash(left, digest)
        return digest


def sorted_leaves(pairs):
    """Leaf hashes in code-point order of patient id; a repeated id is refused."""
    ordered = sorted(pairs, key=lambda pair: pair[0])
    for (a, _), (b, _) in zip(ordered, ordered[1:]):
        _require(a != b, 'DUPLICATE_PATIENT_ID')
    return [leaf_hash(pid, d) for pid, d in ordered]


def patient_root(pairs):
    """The root over (patient_id, patient_digest) pairs, in any order."""
    return root_of(sorted_leaves(pairs))


def streaming_patient_root(sorted_pairs):
    """`patient_root` over pairs already in code-point order of patient id (as DuckDB's binary
    `ORDER BY` gives them), holding O(log n) hashes. Out-of-order or repeated ids are refused."""
    tree, last = StreamingRoot(), None
    for pid, d in sorted_pairs:
        _require(last is None or pid > last, 'PATIENTS_NOT_IN_ORDER' if last is not None and pid < last
                 else 'DUPLICATE_PATIENT_ID')
        tree.push(leaf_hash(pid, d))
        last = pid
    return tree.root(), tree.count


def bucket_tree(pairs, bucket_of, buckets):
    """Two-level form: {bucket: (root, count)} for every bucket 0..buckets-1, and the top root over
    the bucket roots in bucket order (empty buckets as the empty root)."""
    _require(isinstance(buckets, int) and buckets >= 1, 'INVALID_BUCKET_COUNT')
    grouped = {b: [] for b in range(buckets)}
    for pid, d in pairs:
        b = bucket_of(pid)
        _require(isinstance(b, int) and 0 <= b < buckets, 'INVALID_BUCKET')
        grouped[b].append((pid, d))
    roots = {b: (patient_root(members), len(members)) for b, members in grouped.items()}
    top = root_of([roots[b][0] for b in range(buckets)])
    return roots, top


def input_digest(header_bytes, pairs, *, bucket_of=None, buckets=None):
    """sha256 of `PROFILE`, the caller's canonical header bytes, the root and the patient count.

    Flat (one tree over every patient) unless `bucket_of` and `buckets` are given, in which case
    the two-level root is used and the form names the bucket count. The header's canonical form
    is the caller's (the store's sorted-key compact JSON of profile, dataset, closure, provenance).
    """
    _require(isinstance(header_bytes, bytes) and b'\n' not in header_bytes, 'INVALID_HEADER')
    pairs = list(pairs)
    if bucket_of is None:
        _require(buckets is None, 'INVALID_BUCKET_COUNT')
        top, form = patient_root(pairs), 'flat'
    else:
        top, form = bucket_tree(pairs, bucket_of, buckets)[1], f'buckets={buckets}'
    text = f'{PROFILE}\t{form}\n'.encode('ascii') + header_bytes + b'\n' + f'{top.hex()}\t{len(pairs)}\n'.encode('ascii')
    return hashlib.sha256(text).hexdigest()


# ---------------------------------------------------------------- inclusion proofs (RFC 9162 section 2.1.3)

def inclusion_proof(leaves, index):
    """The audit path for leaf `index` in the tree over `leaves`, bottom up."""
    leaves = list(leaves)
    _require(0 <= index < len(leaves), 'INDEX_OUT_OF_RANGE')

    def path(lo, hi, m):
        n = hi - lo
        if n == 1:
            return []
        k = _split(n)
        if m < k:
            return path(lo, lo + k, m) + [root_of(leaves[lo + k:hi])]
        return path(lo + k, hi, m - k) + [root_of(leaves[lo:lo + k])]

    return path(0, len(leaves), index)


def verify_inclusion(leaf, index, size, proof, root):
    """Whether `proof` shows `leaf` at `index` in a tree of `size` leaves with this root."""
    if not 0 <= index < size:
        return False
    fn, sn, digest = index, size - 1, leaf
    for sibling in proof:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            digest = node_hash(sibling, digest)
            if not fn & 1:
                while fn and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            digest = node_hash(digest, sibling)
        fn >>= 1
        sn >>= 1
    return sn == 0 and digest == root
