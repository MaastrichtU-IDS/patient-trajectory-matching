import Std
set_option warningAsError true

/-! Semantic normal form for standard unary-datatype OWL 2 DL. Input validation,
import closure and normalization are specified in the formal-semantics appendix.
DatatypeMap is a fixed semantic parameter, not a selectable satisfaction test. -/
namespace Proposal.OWL
abbrev Name := String
inductive Individual where
  | named : Name → Individual
  | anonymous : Name → Individual
inductive OP where
  | named : Name → OP
  | top | bottom
  | inverse : OP → OP
inductive DP where
  | named : Name → DP
  | top | bottom
inductive DR where
  | named : Name → DR
  | top
  | intersection : DR → DR → DR
  | union : DR → DR → DR
  | complement : DR → DR
  | oneOf : List Name → DR
  | restriction : Name → List (Name × Name) → DR
inductive CE where
  | named : Name → CE
  | top | bottom
  | intersection : CE → CE → CE
  | union : CE → CE → CE
  | complement : CE → CE
  | oneOf : List Individual → CE
  | someObject : OP → CE → CE
  | allObject : OP → CE → CE
  | hasObject : OP → Individual → CE
  | self : OP → CE
  | minObject : Nat → OP → CE → CE
  | maxObject : Nat → OP → CE → CE
  | someData : DP → DR → CE
  | allData : DP → DR → CE
  | hasData : DP → Name → CE
  | minData : Nat → DP → DR → CE
  | maxData : Nat → DP → DR → CE

/-- Literal names are keys for well-typed lexical-form/datatype pairs. Facet
names are resolved against the datatype map before construction. -/
structure DatatypeMap (D : Type) where
  builtin : Name → Prop
  valueSpace : Name → D → Prop
  literal : Name → D
  facet : Name → D → D → Prop

/-- A fixed standard datatype map may be realized inside a larger data domain.
The base carrier contains its required values; extra data values remain available. -/
structure DataExtension {V D : Type} (base : DatatypeMap V) (actual : DatatypeMap D) where
  embed : V → D
  injective : Function.Injective embed
  builtin : ∀ n, actual.builtin n ↔ base.builtin n
  values : ∀ n, base.builtin n → ∀ d,
    actual.valueSpace n d ↔ ∃ v, base.valueSpace n v ∧ embed v = d
  literals : ∀ n, actual.literal n = embed (base.literal n)
  facets : ∀ f v d, actual.facet f (embed v) d ↔
    ∃ w, base.facet f v w ∧ embed w = d

/-- The object/data universe is the tagged sum O ⊕ D. Punning uses separate
interpretation functions. NAMED can include more than named-IRI denotations. -/
structure Interpretation (O D : Type) where
  object_nonempty : Nonempty O
  data_nonempty : Nonempty D
  cls : Name → O → Prop
  obj : Name → O → O → Prop
  data : Name → O → D → Prop
  individual : Individual → O
  datatype : Name → D → Prop
  named : O → Prop
  named_included : ∀ n, named (individual (.named n))

def DatatypeCorrect {O D} (dm : DatatypeMap D) (i : Interpretation O D) : Prop :=
  ∀ n, dm.builtin n → ∀ d, i.datatype n d ↔ dm.valueSpace n d

def op {O D} (i : Interpretation O D) : OP → O → O → Prop
  | .named n => i.obj n
  | .top => fun _ _ => True
  | .bottom => fun _ _ => False
  | .inverse r => fun x y => op i r y x

def dp {O D} (i : Interpretation O D) : DP → O → D → Prop
  | .named n => i.data n
  | .top => fun _ _ => True
  | .bottom => fun _ _ => False

def dr {O D} (dm : DatatypeMap D) (i : Interpretation O D) : DR → D → Prop
  | .named n => i.datatype n
  | .top => fun _ => True
  | .intersection a b => fun d => dr dm i a d ∧ dr dm i b d
  | .union a b => fun d => dr dm i a d ∨ dr dm i b d
  | .complement a => fun d => ¬dr dm i a d
  | .oneOf ns => fun d => ∃ n ∈ ns, dm.literal n = d
  | .restriction n fs => fun d => i.datatype n d ∧
      ∀ f ∈ fs, dm.facet f.1 (dm.literal f.2) d

/-- Cardinality counts distinct domain elements, including unnamed elements. -/
def AtLeast {A : Type} (n : Nat) (p : A → Prop) : Prop :=
  ∃ f : Fin n → A, Function.Injective f ∧ ∀ k, p (f k)
def AtMost {A : Type} (n : Nat) (p : A → Prop) : Prop := ¬AtLeast (n + 1) p

def ce {O D} (dm : DatatypeMap D) (i : Interpretation O D) : CE → O → Prop
  | .named n => i.cls n
  | .top => fun _ => True
  | .bottom => fun _ => False
  | .intersection a b => fun x => ce dm i a x ∧ ce dm i b x
  | .union a b => fun x => ce dm i a x ∨ ce dm i b x
  | .complement a => fun x => ¬ce dm i a x
  | .oneOf ns => fun x => ∃ n ∈ ns, i.individual n = x
  | .someObject r c => fun x => ∃ y, op i r x y ∧ ce dm i c y
  | .allObject r c => fun x => ∀ y, op i r x y → ce dm i c y
  | .hasObject r a => fun x => op i r x (i.individual a)
  | .self r => fun x => op i r x x
  | .minObject n r c => fun x => AtLeast n (fun y => op i r x y ∧ ce dm i c y)
  | .maxObject n r c => fun x => AtMost n (fun y => op i r x y ∧ ce dm i c y)
  | .someData p r => fun x => ∃ d, dp i p x d ∧ dr dm i r d
  | .allData p r => fun x => ∀ d, dp i p x d → dr dm i r d
  | .hasData p l => fun x => dp i p x (dm.literal l)
  | .minData n p r => fun x => AtLeast n (fun d => dp i p x d ∧ dr dm i r d)
  | .maxData n p r => fun x => AtMost n (fun d => dp i p x d ∧ dr dm i r d)

/-- Normalized logical axioms; see the complete normalization table in A.3. -/
inductive Axiom where
  | subClass : CE → CE → Axiom
  | subObject : List OP → OP → Axiom
  | disjointObject : OP → OP → Axiom
  | reflexive : OP → Axiom
  | irreflexive : OP → Axiom
  | subData : DP → DP → Axiom
  | disjointData : DP → DP → Axiom
  | dataRange : DP → DR → Axiom
  | datatypeDefinition : Name → DR → Axiom
  | key : CE → List OP → List DP → Axiom
  | same : Individual → Individual → Axiom
  | different : Individual → Individual → Axiom
  | classAssertion : CE → Individual → Axiom
  | objectAssertion : Bool → OP → Individual → Individual → Axiom
  | dataAssertion : Bool → DP → Individual → Name → Axiom

def chain {O D} (i : Interpretation O D) : List OP → O → O → Prop
  | [] => fun x y => x = y
  | r :: rs => fun x z => ∃ y, op i r x y ∧ chain i rs y z

def satisfiesAxiom {O D} (dm : DatatypeMap D) (i : Interpretation O D) : Axiom → Prop
  | .subClass a b => ∀ x, ce dm i a x → ce dm i b x
  | .subObject rs r => ∀ x y, chain i rs x y → op i r x y
  | .disjointObject r s => ∀ x y, ¬(op i r x y ∧ op i s x y)
  | .reflexive r => ∀ x, op i r x x
  | .irreflexive r => ∀ x, ¬op i r x x
  | .subData p q => ∀ x d, dp i p x d → dp i q x d
  | .disjointData p q => ∀ x d, ¬(dp i p x d ∧ dp i q x d)
  | .dataRange p r => ∀ x d, dp i p x d → dr dm i r d
  | .datatypeDefinition n r => ∀ d, i.datatype n d ↔ dr dm i r d
  | .key c rs ps => ∀ x y, ce dm i c x → i.named x → ce dm i c y → i.named y →
      (∀ r ∈ rs, ∃ z, i.named z ∧ op i r x z ∧ op i r y z) →
      (∀ p ∈ ps, ∃ d, dp i p x d ∧ dp i p y d) → x = y
  | .same a b => i.individual a = i.individual b
  | .different a b => i.individual a ≠ i.individual b
  | .classAssertion c a => ce dm i c (i.individual a)
  | .objectAssertion positive r a b =>
      if positive then op i r (i.individual a) (i.individual b)
      else ¬op i r (i.individual a) (i.individual b)
  | .dataAssertion positive p a l =>
      if positive then dp i p (i.individual a) (dm.literal l)
      else ¬dp i p (i.individual a) (dm.literal l)

abbrev Ontology := List Axiom

def Satisfies {O D} (dm : DatatypeMap D) (i : Interpretation O D) (k : Ontology) : Prop :=
  DatatypeCorrect dm i ∧ ∀ a ∈ k, satisfiesAxiom dm i a

/-- W3C §2.4: anonymous denotations may be reassigned, with all other
interpretation components fixed. Anonymous names are standardized apart. -/
def rebind {O D} (i : Interpretation O D) (anon : Name → O) : Interpretation O D :=
  { i with
    individual := fun a => match a with
      | .named n => i.individual (.named n)
      | .anonymous n => anon n
    named_included := i.named_included }

def Models {O D} (dm : DatatypeMap D) (i : Interpretation O D) (k : Ontology) : Prop :=
  ∃ anon, Satisfies dm (rebind i anon) k

theorem satisfies_models {O D} {dm : DatatypeMap D} {i : Interpretation O D} {k}
    (h : Satisfies dm i k) : Models dm i k := by
  refine ⟨fun n => i.individual (.anonymous n), ?_⟩
  have eq : rebind i (fun n => i.individual (.anonymous n)) = i := by
    cases i
    simp only [rebind]
    congr 1
    funext a
    cases a <;> rfl
  rw [eq]
  exact h

/-- Checks that object-key witnesses retain the NAMED guard. -/
theorem named_key_identifies {O D} {dm : DatatypeMap D} {i : Interpretation O D}
    {c r x y} (h : satisfiesAxiom dm i (.key c [r] []))
    (hx : ce dm i c x) (nx : i.named x) (hy : ce dm i c y) (ny : i.named y)
    (shared : ∃ z, i.named z ∧ op i r x z ∧ op i r y z) : x = y := by
  apply h x y hx nx hy ny
  · intro s hs
    simp only [List.mem_cons, List.not_mem_nil, or_false] at hs
    subst s
    exact shared
  · intro p hp
    exact False.elim (List.not_mem_nil hp)

theorem minimum_one {A : Type} (p : A → Prop) : AtLeast 1 p ↔ ∃ x, p x := by
  constructor
  · rintro ⟨f, _, h⟩
    exact ⟨f 0, h 0⟩
  · rintro ⟨x, h⟩
    exact ⟨fun _ => x, fun _ _ _ => Subsingleton.elim _ _, fun _ => h⟩

/-! Finite semantic regression fixtures, independent of the temporal theory.
Both individual names denote object 0. Object 1 is an unnamed successor. -/
private def fixtureMap : DatatypeMap Bool where
  builtin := fun _ => False
  valueSpace := fun _ _ => False
  literal := fun _ => false
  facet := fun _ _ _ => False

private def fixture : Interpretation (Fin 2) Bool where
  object_nonempty := ⟨0⟩
  data_nonempty := ⟨false⟩
  cls := fun _ _ => True
  obj := fun _ _ _ => True
  data := fun _ _ _ => False
  individual := fun _ => 0
  datatype := fun _ _ => False
  named := fun x => x = 0
  named_included := fun _ => rfl

example : satisfiesAxiom fixtureMap fixture (.same (.named "a") (.named "b")) := rfl

example : ce fixtureMap fixture (.minObject 2 (.named "r") .top) 0 :=
  ⟨id, fun _ _ h => h, fun _ => ⟨True.intro, True.intro⟩⟩

example : satisfiesAxiom fixtureMap fixture (.key .top [] []) := by
  intro x y _ hx _ hy _ _
  exact hx.trans hy.symm

example : satisfiesAxiom fixtureMap fixture
    (.dataAssertion false (.named "p") (.named "a") "literal") := by
  intro h
  exact h

#print axioms minimum_one
#print axioms satisfies_models
#print axioms named_key_identifies
end Proposal.OWL
