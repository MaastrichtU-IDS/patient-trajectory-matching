"""Tests for the Merkle tree over patients (patterns/merkle_patients.py)."""
import hashlib
import random
import unittest

from . import execution_store_reference as ref
from . import merkle_patients as mp


def digest_of(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def pairs(n, seed=0):
    rng = random.Random(seed)
    ids = rng.sample(range(10 * n + 10), n)
    return [(f'P{i:05d}', digest_of(f'patient {i}')) for i in ids]


def naive_root(leaves):
    """RFC 6962 written again, recursively over slices, as the oracle."""
    if not leaves:
        return hashlib.sha256(b'').digest()
    if len(leaves) == 1:
        return leaves[0]
    k = 1
    while k * 2 < len(leaves):
        k *= 2
    return hashlib.sha256(b'\x01' + naive_root(leaves[:k]) + naive_root(leaves[k:])).digest()


class RfcConformanceTests(unittest.TestCase):
    # The Certificate Transparency reference vectors (RFC 6962 test data, as used by the CT
    # implementations): raw leaf inputs and the tree roots over the first 1..8 of them.
    INPUTS = [b'', b'\x00', b'\x10', b'\x20\x21', b'\x30\x31', b'\x40\x41\x42\x43',
              bytes(range(0x50, 0x58)), bytes(range(0x60, 0x70))]
    ROOTS = ['6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d',
             'fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125',
             'aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77',
             'd37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7',
             '4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4',
             '76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef',
             'ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c',
             '5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328']

    def test_reference_vectors(self):
        leaves = [hashlib.sha256(b'\x00' + x).digest() for x in self.INPUTS]
        for n, want in enumerate(self.ROOTS, 1):
            self.assertEqual(mp.root_of(leaves[:n]).hex(), want, n)
            tree = mp.StreamingRoot()
            for leaf in leaves[:n]:
                tree.push(leaf)
            self.assertEqual(tree.root().hex(), want, n)

    def test_empty_tree(self):
        self.assertEqual(mp.root_of([]).hex(), 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
        self.assertEqual(mp.StreamingRoot().root(), mp.EMPTY_ROOT)
        self.assertEqual(mp.patient_root([]), mp.EMPTY_ROOT)


class RootTests(unittest.TestCase):
    def test_batch_streaming_and_naive_agree(self):
        for n in list(range(0, 70)) + [127, 128, 129, 1000, 1025]:
            leaves = mp.sorted_leaves(pairs(n, seed=n))
            want = naive_root(leaves)
            self.assertEqual(mp.root_of(leaves), want, n)
            tree = mp.StreamingRoot()
            for leaf in leaves:
                tree.push(leaf)
            self.assertEqual((tree.root(), tree.count), (want, n), n)
            got, count = mp.streaming_patient_root(sorted(pairs(n, seed=n)))
            self.assertEqual((got, count), (want, n), n)

    def test_order_free_and_sensitive_to_content(self):
        base = pairs(50)
        shuffled = list(base)
        random.Random(1).shuffle(shuffled)
        self.assertEqual(mp.patient_root(base), mp.patient_root(shuffled))
        changed = list(base)
        changed[7] = (changed[7][0], digest_of('another'))
        self.assertNotEqual(mp.patient_root(base), mp.patient_root(changed))
        self.assertNotEqual(mp.patient_root(base), mp.patient_root(base[:-1]))
        renamed = list(base)
        renamed[3] = (renamed[3][0] + 'x', renamed[3][1])
        self.assertNotEqual(mp.patient_root(base), mp.patient_root(renamed))

    def test_lone_last_node_is_not_duplicated(self):
        # Bitcoin-style trees pair a lone node with itself, so [a, b, c] and [a, b, c, c] collide;
        # here a repeated id is refused, and the leaf lists' roots differ anyway.
        leaves = mp.sorted_leaves(pairs(3))
        self.assertNotEqual(mp.root_of(leaves), mp.root_of(leaves + leaves[-1:]))

    def test_leaf_and_node_domains_are_separate(self):
        # Leaves hash 0x00 || data and inner nodes 0x01 || left || right, so an inner node's preimage
        # can never be read as a leaf's.
        d = digest_of('x')
        self.assertEqual(mp.leaf_hash('p', d), hashlib.sha256(b'\x00p\t' + d.encode()).digest())
        a, b = mp.sorted_leaves(pairs(2))
        self.assertEqual(mp.node_hash(a, b), hashlib.sha256(b'\x01' + a + b).digest())

    def test_code_point_order(self):
        # Code-point order is UTF-8 byte order: 'Z' < 'a' < 'é'; not locale or case-folded order.
        ids = ['é1', 'a1', 'Z1', 'b']
        got = mp.patient_root([(i, digest_of(i)) for i in ids])
        want = mp.root_of([mp.leaf_hash(i, digest_of(i)) for i in ['Z1', 'a1', 'b', 'é1']])
        self.assertEqual(got, want)
        self.assertEqual(sorted(ids), sorted(ids, key=lambda s: s.encode('utf-8')))

    def test_refusals(self):
        d = digest_of('x')
        for bad in ('', 'a\tb', 'a\nb', 'a\rb', 7):
            with self.assertRaisesRegex(mp.MerkleError, 'INVALID_PATIENT_ID'):
                mp.leaf_hash(bad, d)
        for bad in ('ABC', d.upper(), d[:-1], None):
            with self.assertRaisesRegex(mp.MerkleError, 'INVALID_PATIENT_DIGEST'):
                mp.leaf_hash('p', bad)
        with self.assertRaisesRegex(mp.MerkleError, 'DUPLICATE_PATIENT_ID'):
            mp.patient_root([('p', d), ('p', d)])
        with self.assertRaisesRegex(mp.MerkleError, 'DUPLICATE_PATIENT_ID'):
            mp.streaming_patient_root([('p', d), ('p', d)])
        with self.assertRaisesRegex(mp.MerkleError, 'PATIENTS_NOT_IN_ORDER'):
            mp.streaming_patient_root([('q', d), ('p', d)])


class BucketTreeTests(unittest.TestCase):
    def test_two_level_form(self):
        data = pairs(300, seed=3)
        roots, top = mp.bucket_tree(data, ref.bucket_of, ref.BUCKET_COUNT)
        self.assertEqual(sorted(roots), list(range(ref.BUCKET_COUNT)))
        self.assertEqual(sum(count for _, count in roots.values()), 300)
        for b, (root, _) in roots.items():
            self.assertEqual(root, mp.patient_root([p for p in data if ref.bucket_of(p[0]) == b]))
        self.assertEqual(top, mp.root_of([roots[b][0] for b in range(ref.BUCKET_COUNT)]))
        # Adding a patient changes its own bucket's root and the top, and no other bucket's root.
        extra = ('NEW-PATIENT', digest_of('new'))
        again, top2 = mp.bucket_tree(data + [extra], ref.bucket_of, ref.BUCKET_COUNT)
        touched = ref.bucket_of(extra[0])
        self.assertNotEqual(top, top2)
        for b in range(ref.BUCKET_COUNT):
            (self.assertNotEqual if b == touched else self.assertEqual)(roots[b], again[b])

    def test_empty_buckets_count(self):
        roots, top = mp.bucket_tree([('a', digest_of('a'))], lambda _: 2, 4)
        self.assertEqual([roots[b][1] for b in range(4)], [0, 0, 1, 0])
        self.assertEqual(top, mp.root_of([mp.EMPTY_ROOT, mp.EMPTY_ROOT, roots[2][0], mp.EMPTY_ROOT]))
        with self.assertRaisesRegex(mp.MerkleError, 'INVALID_BUCKET'):
            mp.bucket_tree([('a', digest_of('a'))], lambda _: 4, 4)


class InputDigestTests(unittest.TestCase):
    def test_binds_header_root_count_and_form(self):
        data = pairs(20)
        header = b'{"dataset_id":"x","profile":"p"}'
        flat = mp.input_digest(header, data)
        self.assertEqual(flat, mp.input_digest(header, list(reversed(data))))
        self.assertNotEqual(flat, mp.input_digest(b'{"dataset_id":"y","profile":"p"}', data))
        self.assertNotEqual(flat, mp.input_digest(header, data[:-1]))
        bucketed = mp.input_digest(header, data, bucket_of=ref.bucket_of, buckets=ref.BUCKET_COUNT)
        self.assertNotEqual(flat, bucketed)
        expected = hashlib.sha256(b'merkle-patients-1\tflat\n' + header + b'\n'
                                  + (mp.patient_root(data).hex() + '\t20\n').encode()).hexdigest()
        self.assertEqual(flat, expected)
        with self.assertRaisesRegex(mp.MerkleError, 'INVALID_HEADER'):
            mp.input_digest(b'a\nb', data)

    def test_uses_the_store_reference_patient_digest(self):
        # The leaves can be the reference's own patient digests: the tree only needs (id, digest).
        patients = [{'patient_id': f'p{i}', 'clock_id': 'c', 'events': [], 'coverage': []} for i in range(5)]
        data = [(p['patient_id'], ref.patient_digest(p)) for p in patients]
        self.assertEqual(len(mp.patient_root(data)), 32)


class InclusionProofTests(unittest.TestCase):
    def test_every_leaf_of_many_sizes(self):
        for n in list(range(1, 40)) + [64, 65, 100]:
            leaves = mp.sorted_leaves(pairs(n, seed=n))
            root = mp.root_of(leaves)
            for i in range(n):
                proof = mp.inclusion_proof(leaves, i)
                self.assertTrue(mp.verify_inclusion(leaves[i], i, n, proof, root), (n, i))
                self.assertLessEqual(len(proof), max(1, (n - 1).bit_length()))
                if n > 1:
                    self.assertFalse(mp.verify_inclusion(leaves[(i + 1) % n], i, n, proof, root), (n, i))
                    self.assertFalse(mp.verify_inclusion(leaves[i], (i + 1) % n, n, proof, root), (n, i))
                self.assertFalse(mp.verify_inclusion(leaves[i], i, n, proof, mp.EMPTY_ROOT), (n, i))
            self.assertFalse(mp.verify_inclusion(leaves[0], n, n, [], root))
        # A proof does not bind the tree size by itself (index 0 has one path for sizes 3 and 4);
        # the size is bound by the input digest, which hashes the count beside the root.


if __name__ == '__main__':
    unittest.main()
