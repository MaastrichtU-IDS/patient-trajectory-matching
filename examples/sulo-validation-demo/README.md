# Validating against SULO: two files that pass the obvious check

Two deliberately flawed toy files, and a checker that catches what the obvious
invocation misses. Everything here runs in a few seconds against pinned SULO 0.2.14.

```sh
python examples/sulo-validation-demo/check.py \
  --sulo ontology/vendor/sulo-0.2.14.ttl \
  --target examples/sulo-validation-demo/toy-extension.ttl \
  --robot /path/to/robot.jar
```

## The two flaws

| file | what is wrong with it |
|---|---|
| `toy-extension.ttl` | references `sulo:isLocatedIn`, which SULO removed at 0.2.8 |
| `toy-data.ttl` | asserts one individual into two classes SULO declares disjoint |

Neither file is malformed. Both parse. Neither is caught by the way people usually check.

## What each check finds

| target | 1. dangling terms | 2. DL profile | 3. consistency |
|---|---|---|---|
| `toy-extension.ttl` | **`sulo:isLocatedIn`** | in profile | consistent |
| `toy-data.ttl` | none | in profile | **inconsistent** |

The first row is the uncomfortable one: the extension is **in the OWL 2 DL profile and
consistent under HermiT, and still broken.** No reasoner will ever object to a reference
to a property that does not exist. It is not a logical error.

## Why the order matters

Each step destroys the evidence the previous one needs.

**Dangling terms must be read from the files, before merging.** `robot merge` declares
any entity it finds undeclared, so after a merge `sulo:isLocatedIn` appears as a property
with a declaration — one that exists nowhere in SULO. The checker prints this as it
happens:

```
NOT DEFINED IN THE CORE  https://w3id.org/sulo/isLocatedIn
...and after merging, 1 of them now appear declared,
   which is why this check runs first
```

**Consistency must be checked after merging.** A target read on its own declares none of
the SULO properties it uses, so OWLAPI parses those triples as annotation assertions.
Annotations carry no semantics, so the reasoner has nothing to contradict:

```sh
robot reason --input toy-data.ttl                              # exit 0  "consistent"
robot merge --input sulo.ttl --input toy-data.ttl reason       # exit 1  inconsistent
```

The same file. One of those commands accepts an individual that SULO's own
`AllDisjointClasses` axiom forbids.

`validate-profile` on a target alone is loud rather than silent, but no more useful: it
reports the whole SULO vocabulary as undeclared, which is true and beside the point.

## What this does not do

It checks that a target is internally coherent against the core it extends. It says
nothing about whether the modelling is *right* — whether `toy:Admission` should be a
`sulo:Process` at all is a question for a reviewer, not a reasoner.

A third kind of check is worth adding for any target with instance data: a **negative
control**, built from the target's own content, that the reasoner must reject. Without
one, a check that has quietly stopped reading the data is indistinguishable from a
target that is clean. Our own OWL gate runs 73 such controls across 20 merged documents;
see `.github/scripts/check_owl_consistency.py`.
