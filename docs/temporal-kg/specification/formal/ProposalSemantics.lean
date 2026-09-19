import IntegratedSemantics
import OWLDirectSemantics
set_option warningAsError true

namespace Proposal
open TemporalIntegration

/-- Explicit signature map, interpreted through OWL Direct Semantics. -/
structure SignatureMap where
  chronoid : OWL.CE
  boundary : OWL.CE
  leftBoundary : OWL.CE
  rightBoundary : OWL.CE
  hasLeft : OWL.OP
  hasRight : OWL.OP
  coincides : OWL.OP
  temporalPart : OWL.OP

def view {O D} (dm : OWL.DatatypeMap D) (i : OWL.Interpretation O D)
    (v : SignatureMap) : OWLView O where
  chronoid := OWL.ce dm i v.chronoid
  boundary := OWL.ce dm i v.boundary
  leftBoundary := OWL.ce dm i v.leftBoundary
  rightBoundary := OWL.ce dm i v.rightBoundary
  hasLeft := OWL.op i v.hasLeft
  hasRight := OWL.op i v.hasRight
  coincides := OWL.op i v.coincides
  temporalPart := OWL.op i v.temporalPart

/-- Strict order of coincidence classes, expressed without quotient choices. -/
def Earlier {C B} (t : TemporalSignature C B) (a b : B) : Prop :=
  ∃ c, t.coinc a (t.first c) ∧ t.coinc b (t.last c)

/-- Partial exact rational chart, closed under coincidence and between-ness.
Coordinates outside the chart domain have no semantic use. -/
structure Chart {C B} (t : TemporalSignature C B) where
  domain : B → Prop
  coordinate : B → Rat
  coincidence_closed : ∀ a b, domain a → t.coinc a b → domain b
  exact : ∀ a b, domain a → domain b → (coordinate a = coordinate b ↔ t.coinc a b)
  ordered : ∀ a b, domain a → domain b → (coordinate a < coordinate b ↔ Earlier t a b)
  convex : ∀ a b c, domain a → domain c → Earlier t a b → Earlier t b c → domain b

/-- Finite admitted source normal form. Finite indices represent distinct
record identifiers. Boundary ownership is total; references may share variables.
Metadata, lexical bounds and scope checks precede this normal form (Appendix A.5). -/
structure Source where
  variables : Nat
  intervals : Nat
  boundaries : Nat
  events : Nat
  clocks : Nat
  scopes : Nat
  scopeClock : Fin scopes → Fin clocks
  variableScope : Fin variables → Fin scopes
  lower : Fin variables → Int
  upper : Fin variables → Int
  differences : List (Fin variables × Fin variables × Int)
  owner : Fin boundaries → Fin intervals
  isLeft : Fin boundaries → Bool
  coordVar : Fin boundaries → Fin variables
  first : Fin intervals → Fin boundaries
  last : Fin intervals → Fin boundaries
  eventIRI : Fin events → OWL.Name
  eventScope : Fin events → Fin scopes
  extent : Fin events → Sum (Fin intervals) (Fin boundaries)
  intervalIRI : Fin intervals → Option OWL.Name
  boundaryIRI : Fin boundaries → Option OWL.Name

/-- A satisfying interpretation with existential anonymous witnesses chosen.
Domains may vary; the standard datatype map is embedded with fixed meanings. -/
structure World {D : Type} (dm : OWL.DatatypeMap D) (k : OWL.Ontology)
    (v : SignatureMap) (s : Source) where
  Data : Type
  datatypeMap : OWL.DatatypeMap Data
  data_extension : OWL.DataExtension dm datatypeMap
  O : Type
  C : Type
  B : Type
  owl : OWL.Interpretation O Data
  owl_satisfies : OWL.Satisfies datatypeMap owl k
  time : TemporalSignature C B
  btc : BTC time
  bridge : Bridge time (view datatypeMap owl v)
  oriented : ∀ o, ¬(OWL.ce datatypeMap owl v.leftBoundary o ∧ OWL.ce datatypeMap owl v.rightBoundary o)
  chart : Fin s.clocks → Chart time
  chronoid : Fin s.intervals → C
  boundary : Fin s.boundaries → B
  assignment : Fin s.variables → Int
  ownership : ∀ b, boundary b =
    if s.isLeft b then time.first (chronoid (s.owner b)) else time.last (chronoid (s.owner b))
  located : ∀ b, (chart (s.scopeClock (s.variableScope (s.coordVar b)))).domain (boundary b)
  coordinate : ∀ b,
    (chart (s.scopeClock (s.variableScope (s.coordVar b)))).coordinate (boundary b) =
    Rat.ofInt (assignment (s.coordVar b))
  bounds : ∀ x, s.lower x ≤ assignment x ∧ assignment x ≤ s.upper x
  differences : ∀ d ∈ s.differences, assignment d.1 - assignment d.2.1 ≤ d.2.2
  proper_intervals : ∀ c, assignment (s.coordVar (s.first c)) < assignment (s.coordVar (s.last c))
  interval_names : ∀ c n, s.intervalIRI c = some n →
    owl.individual (.named n) = bridge.chron (chronoid c)
  boundary_names : ∀ b n, s.boundaryIRI b = some n →
    owl.individual (.named n) = bridge.boundary (boundary b)

/-- Input well-formedness clauses that affect denotation; syntax/provenance
requirements are additionally imposed by the main specification. -/
def Source.WellFormed (s : Source) : Prop :=
  (∀ x, s.lower x ≤ s.upper x) ∧
  (∀ c, s.owner (s.first c) = c ∧ s.isLeft (s.first c) = true ∧
        s.owner (s.last c) = c ∧ s.isLeft (s.last c) = false ∧
        s.variableScope (s.coordVar (s.first c)) = s.variableScope (s.coordVar (s.last c))) ∧
  (∀ b, if s.isLeft b then b = s.first (s.owner b) else b = s.last (s.owner b)) ∧
  (∀ d ∈ s.differences, s.variableScope d.1 = s.variableScope d.2.1) ∧
  (∀ e, s.eventScope e = match s.extent e with
    | .inl c => s.variableScope (s.coordVar (s.first c))
    | .inr b => s.variableScope (s.coordVar b))

def Feasible (s : Source) (a : Fin s.variables → Int) : Prop :=
  (∀ x, s.lower x ≤ a x ∧ a x ≤ s.upper x) ∧
  (∀ d ∈ s.differences, a d.1 - a d.2.1 ≤ d.2.2) ∧
  (∀ c, a (s.coordVar (s.first c)) < a (s.coordVar (s.last c)))

theorem projection_sound {D} {dm : OWL.DatatypeMap D} {k v s}
    (m : World dm k v s) : Feasible s m.assignment :=
  ⟨m.bounds, m.differences, m.proper_intervals⟩

/-- Coordinates, after a fixed eligible binding has resolved slot references. -/
inductive Atom (n : Nat) where
  | le : Fin n → Fin n → Atom n
  | lt : Fin n → Fin n → Atom n
  | eq : Fin n → Fin n → Atom n
  | window : Fin n → Fin n → Int → Int → Atom n
  | overlap : Fin n → Fin n → Fin n → Fin n → Int → Atom n

def Atom.holds {n} (a : Fin n → Int) : Atom n → Prop
  | .le x y => a x ≤ a y
  | .lt x y => a x < a y
  | .eq x y => a x = a y
  | .window x y l u => l ≤ a y - a x ∧ a y - a x ≤ u
  | .overlap s e u v d => d ≤ min (a e) (a v) - max (a s) (a u)

def Conjunction {n} (a : Fin n → Int) (q : List (Atom n)) : Prop :=
  ∀ atom ∈ q, atom.holds a

/-- Standard OWL-supported named-class selection, independent of temporal worlds. -/
def Supported {D} (dm : OWL.DatatypeMap D) (k : OWL.Ontology)
    (c : OWL.CE) (n : OWL.Name) : Prop :=
  ∀ (Data : Type) (actual : OWL.DatatypeMap Data), OWL.DataExtension dm actual →
    ∀ (O : Type) (i : OWL.Interpretation O Data), OWL.Models actual i k →
      OWL.ce actual i c (i.individual (.named n))

structure Request (s : Source) where
  slots : Nat
  scope : Fin s.scopes
  intervalSlot : Fin slots → Bool
  required : Fin slots → OWL.CE

/-- One fixed binding of record handles. Multiple records can name one process;
no unique-name axiom is added to OWL. -/
def Eligible {D} (dm : OWL.DatatypeMap D) (k : OWL.Ontology) {s : Source}
    (q : Request s) (mu : Fin q.slots → Fin s.events) : Prop :=
  Function.Injective mu ∧ ∀ x,
    s.eventScope (mu x) = q.scope ∧
    (match s.extent (mu x) with
      | .inl _ => q.intervalSlot x = true
      | .inr _ => q.intervalSlot x = false) ∧
    Supported dm k (q.required x) (s.eventIRI (mu x))

/-- Classical integrated consequence. Operational certainty separately requires
an inhabited compatible-model class to avoid vacuous service answers. -/
def Entails {D} (dm : OWL.DatatypeMap D) (k : OWL.Ontology)
    (v : SignatureMap) (s : Source) (sentence : World dm k v s → Prop) : Prop :=
  ∀ m, sentence m

def Consistent {D} (dm : OWL.DatatypeMap D) (k : OWL.Ontology)
    (v : SignatureMap) (s : Source) : Prop := s.WellFormed ∧ Nonempty (World dm k v s)

def Possible {D} (dm : OWL.DatatypeMap D) (k : OWL.Ontology)
    (v : SignatureMap) (s : Source) (q : List (Atom s.variables)) : Prop :=
  s.WellFormed ∧ ∃ m : World dm k v s, Conjunction m.assignment q

def Certain {D} (dm : OWL.DatatypeMap D) (k : OWL.Ontology)
    (v : SignatureMap) (s : Source) (q : List (Atom s.variables)) : Prop :=
  Consistent dm k v s ∧ Entails dm k v s (fun m => Conjunction m.assignment q)

/-- The candidate fixes both the catalogue option and binding before worlds vary. -/
structure Candidate (s : Source) (q : Request s) where
  binding : Fin q.slots → Fin s.events
  conditions : List (Atom s.variables)
  cost : Rat
  changes : Nat

def Robust {D} (dm : OWL.DatatypeMap D) (k : OWL.Ontology) (v : SignatureMap)
    {s : Source} (q : Request s) (catalogue : List (Candidate s q))
    (budget : Rat) (limit : Nat) (candidate : Candidate s q) : Prop :=
  candidate ∈ catalogue ∧ 0 ≤ candidate.cost ∧ candidate.cost ≤ budget ∧
  candidate.changes ≤ limit ∧ Eligible dm k q candidate.binding ∧
  Certain dm k v s candidate.conditions

def Optimal {D} (dm : OWL.DatatypeMap D) (k : OWL.Ontology) (v : SignatureMap)
    {s : Source} (q : Request s) (catalogue : List (Candidate s q))
    (budget : Rat) (limit : Nat) (candidate : Candidate s q) : Prop :=
  Robust dm k v q catalogue budget limit candidate ∧
  ∀ other, Robust dm k v q catalogue budget limit other → candidate.cost ≤ other.cost

/-- Exact coverage normal form for one patient/episode/key/clock. Coordinates
are rational even when admitted endpoints are integers. Observations contribute
no validity interval. Conflicts are checked across all groups before coverage. -/
structure Footprint where
  start : Rat
  finish : Rat

def Footprint.contains (i : Footprint) (r : Rat) : Prop := i.start ≤ r ∧ r < i.finish

def Support (is : List Footprint) (r : Rat) : Prop := ∃ i ∈ is, i.contains r

def ConflictFree (positive negative : List Footprint) : Prop :=
  ∀ r, ¬(Support positive r ∧ Support negative r)

def Completion (positive negative : List Footprint) (f : Rat → Prop) : Prop :=
  (∀ r, Support positive r → f r) ∧ (∀ r, Support negative r → ¬f r)

def Holds (positive negative : List Footprint) (window : Footprint) : Prop :=
  ConflictFree positive negative ∧ window.start < window.finish ∧
  ∀ f, Completion positive negative f → ∀ r, window.contains r → f r

def Violated (positive negative : List Footprint) (window : Footprint) : Prop :=
  ConflictFree positive negative ∧ window.start < window.finish ∧
  ∃ r, window.contains r ∧ Support negative r

def Unknown (positive negative : List Footprint) (window : Footprint) : Prop :=
  ConflictFree positive negative ∧ window.start < window.finish ∧
  ¬Holds positive negative window ∧ ¬Violated positive negative window

/-- Any positive footprint gap admits a false completion, even at unnamed
rational coordinates between named grid points. -/
theorem coverage_iff_positive (p n : List Footprint) (w : Footprint)
    (safe : ConflictFree p n) (proper : w.start < w.finish) :
    Holds p n w ↔ ∀ r, w.contains r → Support p r := by
  constructor
  · intro h
    apply h.2.2 (Support p)
    constructor
    · intro r hr; exact hr
    · intro r hn hp; exact safe r ⟨hp, hn⟩
  · intro h
    refine ⟨safe, proper, ?_⟩
    intro f hf r hr
    exact hf.1 r (h r hr)

theorem certain_possible {D} {dm : OWL.DatatypeMap D} {k v s q}
    (h : Certain dm k v s q) : Possible dm k v s q := by
  obtain ⟨m⟩ := h.1.2
  exact ⟨h.1.1, m, h.2 m⟩

theorem integrated_owl_reduct {D} {dm : OWL.DatatypeMap D} {k v s}
    (m : World dm k v s) : OWL.Models m.datatypeMap m.owl k :=
  OWL.satisfies_models m.owl_satisfies

theorem supported_in_world {D} {dm : OWL.DatatypeMap D} {k v s c n}
    (h : Supported dm k c n) (m : World dm k v s) :
    OWL.ce m.datatypeMap m.owl c (m.owl.individual (.named n)) :=
  h m.Data m.datatypeMap m.data_extension m.O m.owl (integrated_owl_reduct m)

/-- Chart-coordinate equality is a temporal coincidence condition, never an
object-identity test. The existing bridge transfers this to OWL predicates. -/
theorem equal_coordinates_owl_coincidence {D} {dm : OWL.DatatypeMap D} {k v s}
    (m : World dm k v s) (clock : Fin s.clocks) (a b : m.B)
    (ha : (m.chart clock).domain a) (hb : (m.chart clock).domain b)
    (eq : (m.chart clock).coordinate a = (m.chart clock).coordinate b) :
    OWL.op m.owl v.coincides (m.bridge.boundary a) (m.bridge.boundary b) :=
  (m.bridge.coinc_graph a b).mpr (((m.chart clock).exact a b ha hb).mp eq)

theorem inconsistent_never_certain {D} {dm : OWL.DatatypeMap D} {k v s q}
    (h : ¬Consistent dm k v s) : ¬Certain dm k v s q := fun hc => h hc.1

/-- Fixed and world-dependent witnesses have different meanings, even for two
worlds and two candidate events. -/
theorem fixed_witness_counterexample :
    (∀ w : Bool, ∃ e : Bool, e = w) ∧ ¬(∃ e : Bool, ∀ w : Bool, e = w) := by
  constructor
  · intro w; exact ⟨w, rfl⟩
  · rintro ⟨e, h⟩
    exact Bool.false_ne_true ((h false).symm.trans (h true))

#print axioms inconsistent_never_certain
#print axioms fixed_witness_counterexample
#print axioms projection_sound
#print axioms coverage_iff_positive
#print axioms certain_possible
#print axioms integrated_owl_reduct
#print axioms supported_in_world
#print axioms equal_coordinates_owl_coincidence
end Proposal
