import Std
set_option warningAsError true

/-! Typed integration metatheory. BTC is an axiomatic interface, not a model
construction. OWL satisfaction is a parameter intended to be instantiated by
W3C Direct Semantics. No OWL parser or full translator is claimed. -/
namespace TemporalIntegration
universe u v w
structure TemporalSignature (C : Type u) (B : Type v) where
  first : C → B
  last : C → B
  part : C → C → Prop
  coinc : B → B → Prop
namespace TemporalSignature
variable {C : Type u} {B : Type v}
def ProperPart (t : TemporalSignature C B) (c d : C) := t.part c d ∧ c ≠ d
def Starts (t : TemporalSignature C B) (c d : C) := t.ProperPart c d ∧ t.first c = t.first d
def Ends (t : TemporalSignature C B) (c d : C) := t.ProperPart c d ∧ t.last c = t.last d
def During (t : TemporalSignature C B) (c d : C) :=
  t.ProperPart c d ∧ ¬t.Starts c d ∧ ¬t.Ends c d
def Overlap (t : TemporalSignature C B) (c d : C) := ∃ a, t.part a c ∧ t.part a d
def Meets (t : TemporalSignature C B) (c d : C) := t.coinc (t.last c) (t.first d)
def SameTime (t : TemporalSignature C B) (c d : C) :=
  t.coinc (t.first c) (t.first d) ∧ t.coinc (t.last c) (t.last d)
/-- Boundary order induced by chronoids: a is before b when some chronoid begins
where a is and ends where b is. BTC's order on coincidence classes (2014, §8.1)
is this relation; its properties are proved from A18-A20, A26, A27 and A33 below. -/
def Before (t : TemporalSignature C B) (a b : B) :=
  ∃ c, t.coinc a (t.first c) ∧ t.coinc b (t.last c)
end TemporalSignature

/-- Typed relativization of 2014 BTC. The temporal universe is C ⊕ B.
A1,A2,A4-A6 are enforced by sorts; A13-A16 by functions. Other fields carry
source axiom numbers. Nonempty C excludes an empty typed temporal universe. -/
structure BTC {C : Type u} {B : Type v} (t : TemporalSignature C B) : Prop where
  nonempty : Nonempty C
  compatible : ∀ c d, ∃ e, t.part c e ∧ t.part d e -- A3
  part_refl : ∀ c, t.part c c -- A7
  part_antisymm : ∀ c d, t.part c d → t.part d c → c = d -- A8
  part_trans : ∀ c d e, t.part c d → t.part d e → t.part c e -- A9
  future : ∀ c, ∃ d, t.Starts c d -- A10
  past : ∀ c, ∃ d, t.Ends c d -- A11
  inner : ∀ c, ∃ d, t.During d c -- A12
  dependent : ∀ b, ∃ c, t.first c = b ∨ t.last c = b -- A17
  coinc_refl : ∀ b, t.coinc b b -- A18
  coinc_symm : ∀ a b, t.coinc a b → t.coinc b a -- A19
  coinc_trans : ∀ a b c, t.coinc a b → t.coinc b c → t.coinc a c -- A20
  counterpart : ∀ a, ∃ b, a ≠ b ∧ t.coinc a b -- A21
  at_most_two : ∀ a b c, t.coinc a b → t.coinc a c → a = b ∨ a = c ∨ b = c -- A22
  intersection : ∀ c d, t.Overlap c d →
    ∃ e, t.part e c ∧ t.part e d ∧ ∀ a, t.part a c → t.part a d → t.part a e -- A23
  supplement : ∀ c d, ¬t.part c d → ∃ e, t.part e c ∧ ¬t.Overlap e d -- A24
  extensional : ∀ c d, t.SameTime c d → c = d -- A25
  proper : ∀ c, ¬t.coinc (t.first c) (t.last c) -- A26, with endpoint uniqueness
  span : ∀ a b, ¬t.coinc a b → ∃ c,
    (t.coinc a (t.first c) ∧ t.coinc b (t.last c)) ∨
    (t.coinc a (t.last c) ∧ t.coinc b (t.first c)) -- A27
  first_nested : ∀ c d, t.coinc (t.first c) (t.first d) → t.part c d ∨ t.part d c -- A28
  last_nested : ∀ c d, t.coinc (t.last c) (t.last d) → t.part c d ∨ t.part d c -- A29
  embedded : ∀ c d,
    (∃ a, t.first a = t.first d ∧ t.coinc (t.last a) (t.first c)) →
    (∃ b, t.coinc (t.first b) (t.last c) ∧ t.last b = t.last d) → t.During c d -- A30
  start_fragment : ∀ c d, t.part c d → t.first c ≠ t.first d →
    ∃ a, t.Starts a d ∧ t.coinc (t.last a) (t.first c) -- A31
  end_fragment : ∀ c d, t.part c d → t.last c ≠ t.last d →
    ∃ a, t.Ends a d ∧ t.coinc (t.first a) (t.last c) -- A32
  meeting_sum : ∀ c d, t.Meets c d → ∃ e,
    t.first c = t.first e ∧ t.last d = t.last e ∧
    ¬∃ a, t.part a e ∧ ¬t.Overlap a c ∧ ¬t.Overlap a d -- A33
  overlap_sum : ∀ c d, t.Overlap c d → (∃ a, t.Starts a c ∧ ¬t.Overlap a d) →
    ∃ e, t.first c = t.first e ∧ t.last d = t.last e ∧
    ¬∃ a, t.part a e ∧ ¬t.Overlap a c ∧ ¬t.Overlap a d -- A34

/-- The order properties a temporal argument may rely on: strict, respecting
coincidence, with each chronoid's first boundary before its last. -/
structure BTCOrder {C : Type u} {B : Type v} (t : TemporalSignature C B)
    (before : B → B → Prop) : Prop where
  irrefl : ∀ a, ¬before a a
  trans : ∀ a b c, before a b → before b c → before a c
  coinc_left : ∀ a a' b, t.coinc a a' → before a b → before a' b
  coinc_right : ∀ a b b', t.coinc b b' → before a b → before a b'
  first_before_last : ∀ c, before (t.first c) (t.last c)

section Order
variable {C : Type u} {B : Type v} {t : TemporalSignature C B}

theorem before_irrefl (h : BTC t) (a : B) : ¬t.Before a a := by
  rintro ⟨c, hf, hl⟩
  exact h.proper c (h.coinc_trans _ _ _ (h.coinc_symm _ _ hf) hl)

/-- Two chronoids meeting at b sum to one chronoid (A33), so the order composes. -/
theorem before_trans (h : BTC t) (a b d : B) (hab : t.Before a b) (hbd : t.Before b d) :
    t.Before a d := by
  obtain ⟨c₁, ha, hb⟩ := hab
  obtain ⟨c₂, hb', hd⟩ := hbd
  have meets : t.Meets c₁ c₂ := h.coinc_trans _ _ _ (h.coinc_symm _ _ hb) hb'
  obtain ⟨e, hfirst, hlast, _⟩ := h.meeting_sum c₁ c₂ meets
  exact ⟨e, hfirst ▸ ha, hlast ▸ hd⟩

theorem before_coinc_left (h : BTC t) (a a' b : B) (co : t.coinc a a') (hab : t.Before a b) :
    t.Before a' b := by
  obtain ⟨c, ha, hb⟩ := hab
  exact ⟨c, h.coinc_trans _ _ _ (h.coinc_symm _ _ co) ha, hb⟩

theorem before_coinc_right (h : BTC t) (a b b' : B) (co : t.coinc b b') (hab : t.Before a b) :
    t.Before a b' := by
  obtain ⟨c, ha, hb⟩ := hab
  exact ⟨c, ha, h.coinc_trans _ _ _ (h.coinc_symm _ _ co) hb⟩

theorem first_before_last (h : BTC t) (c : C) : t.Before (t.first c) (t.last c) :=
  ⟨c, h.coinc_refl _, h.coinc_refl _⟩

/-- Non-coincident boundaries are ordered one way or the other (A27). -/
theorem before_total (h : BTC t) (a b : B) (distinct : ¬t.coinc a b) :
    t.Before a b ∨ t.Before b a := by
  obtain ⟨c, (⟨ha, hb⟩ | ⟨ha, hb⟩)⟩ := h.span a b distinct
  · exact Or.inl ⟨c, ha, hb⟩
  · exact Or.inr ⟨c, hb, ha⟩

/-- BTC's chronoid-induced order satisfies every BTCOrder property. -/
theorem btc_order (h : BTC t) : BTCOrder t t.Before :=
  ⟨before_irrefl h, before_trans h, before_coinc_left h, before_coinc_right h, first_before_last h⟩
end Order

/-- Exposed predicates of an OWL interpretation. Full satisfaction is external. -/
structure OWLView (O : Type w) where
  chronoid : O → Prop
  boundary : O → Prop
  leftBoundary : O → Prop
  rightBoundary : O → Prop
  hasLeft : O → O → Prop
  hasRight : O → O → Prop
  coincides : O → O → Prop
  temporalPart : O → O → Prop

/-- Shared-symbol interpretation conditions; these connect the same OWL objects
and temporal entities. They are stronger than two independent consistency checks. -/
structure Bridge {C : Type u} {B : Type v} {O : Type w}
    (t : TemporalSignature C B) (owl : OWLView O) where
  chron : C → O
  boundary : B → O
  chron_injective : Function.Injective chron
  boundary_injective : Function.Injective boundary
  sorts_disjoint : ∀ c b, chron c ≠ boundary b
  chron_class : ∀ o, owl.chronoid o ↔ ∃ c, chron c = o
  boundary_class : ∀ o, owl.boundary o ↔ ∃ b, boundary b = o
  left_class : ∀ o, owl.leftBoundary o ↔ ∃ c, boundary (t.first c) = o
  right_class : ∀ o, owl.rightBoundary o ↔ ∃ c, boundary (t.last c) = o
  first_graph : ∀ c b, owl.hasLeft (chron c) (boundary b) ↔ t.first c = b
  last_graph : ∀ c b, owl.hasRight (chron c) (boundary b) ↔ t.last c = b
  coinc_graph : ∀ a b, owl.coincides (boundary a) (boundary b) ↔ t.coinc a b
  part_graph : ∀ c d, owl.temporalPart (chron c) (chron d) ↔ t.part c d
  first_typed : ∀ a b, owl.hasLeft a b → owl.chronoid a ∧ owl.boundary b
  last_typed : ∀ a b, owl.hasRight a b → owl.chronoid a ∧ owl.boundary b
  coinc_typed : ∀ a b, owl.coincides a b → owl.boundary a ∧ owl.boundary b
  part_typed : ∀ a b, owl.temporalPart a b → owl.chronoid a ∧ owl.chronoid b

/-- Instantiate owlSatisfies with the required Direct Semantics interpretation.
Additional finite-source/chart/admission predicates are conjoined by the profile. -/
structure IntegratedModel {C : Type u} {B : Type v} {O : Type w}
    (t : TemporalSignature C B) (owl : OWLView O)
    (owlSatisfies : OWLView O → Prop) where
  owl_model : owlSatisfies owl
  time_model : BTC t
  bridge : Bridge t owl

variable {C : Type u} {B : Type v} {O : Type w}
variable {t : TemporalSignature C B} {owl : OWLView O}
theorem same_time_chronoid_identity (h : BTC t) (c d : C)
    (same : t.SameTime c d) : c = d := h.extensional c d same

/-- Equating the two ends in OWL makes a compatible model impossible. -/
theorem endpoint_collapse_impossible (h : BTC t) (b : Bridge t owl) (c : C) :
    b.boundary (t.first c) ≠ b.boundary (t.last c) := by
  intro eq
  have ends := b.boundary_injective eq
  have co : t.coinc (t.first c) (t.last c) := ends ▸ h.coinc_refl (t.first c)
  exact h.proper c co

/-- Disjoint left/right OWL classes reflect BTC consequence C2. This theorem
uses that projection requirement explicitly rather than claiming to reprove C2. -/
theorem meeting_boundaries_distinct (b : Bridge t owl)
    (disjoint : ∀ o, ¬(owl.leftBoundary o ∧ owl.rightBoundary o)) (c d : C) :
    b.boundary (t.last c) ≠ b.boundary (t.first d) := by
  intro eq
  have hl : owl.leftBoundary (b.boundary (t.first d)) := (b.left_class _).mpr ⟨d, rfl⟩
  have hr : owl.rightBoundary (b.boundary (t.last c)) := (b.right_class _).mpr ⟨c, rfl⟩
  rw [eq] at hr
  exact disjoint _ ⟨hl, hr⟩

/-- Three distinct OWL boundary objects in one coincidence class violate
BTC A22, even if each component theory has a model considered separately. -/
theorem three_coincident_boundaries_impossible (h : BTC t) (j : Bridge t owl)
    (a b c : B)
    (ab : j.boundary a ≠ j.boundary b)
    (ac : j.boundary a ≠ j.boundary c)
    (bc : j.boundary b ≠ j.boundary c)
    (hab : owl.coincides (j.boundary a) (j.boundary b))
    (hac : owl.coincides (j.boundary a) (j.boundary c)) : False := by
  rcases h.at_most_two a b c ((j.coinc_graph a b).mp hab)
      ((j.coinc_graph a c).mp hac) with eq | eq | eq
  · exact ab (congrArg j.boundary eq)
  · exact ac (congrArg j.boundary eq)
  · exact bc (congrArg j.boundary eq)

theorem owl_reduct_sound {sat : OWLView O → Prop}
    (m : IntegratedModel t owl sat) : sat owl := m.owl_model

/-- Solver and integrated possibilities agree under explicit soundness and
extension assumptions. The extension assumption is not proved for the engine. -/
theorem possible_projection {World Assignment : Type}
    (admitted : World → Prop) (feasible : Assignment → Prop)
    (project : World → Assignment) (atom : Assignment → Prop)
    (sound : ∀ w, admitted w → feasible (project w))
    (extend : ∀ a, feasible a → ∃ w, admitted w ∧ project w = a) :
    (∃ w, admitted w ∧ atom (project w)) ↔ (∃ a, feasible a ∧ atom a) := by
  constructor
  · rintro ⟨w, hw, ha⟩
    exact ⟨project w, sound w hw, ha⟩
  · rintro ⟨a, ha, hq⟩
    obtain ⟨w, hw, hp⟩ := extend a ha
    exact ⟨w, hw, hp.symm ▸ hq⟩

theorem certain_projection {World Assignment : Type}
    (admitted : World → Prop) (feasible : Assignment → Prop)
    (project : World → Assignment) (atom : Assignment → Prop)
    (sound : ∀ w, admitted w → feasible (project w))
    (extend : ∀ a, feasible a → ∃ w, admitted w ∧ project w = a) :
    (∀ w, admitted w → atom (project w)) ↔ (∀ a, feasible a → atom a) := by
  constructor
  · intro h a ha
    obtain ⟨w, hw, hp⟩ := extend a ha
    exact hp ▸ h w hw
  · intro h w hw
    exact h (project w) (sound w hw)

example : ∃ (extent : Bool → Unit), false ≠ true ∧ extent false = extent true := by
  exact ⟨fun _ => (), Bool.false_ne_true, rfl⟩

#print axioms before_irrefl
#print axioms before_trans
#print axioms before_total
#print axioms btc_order
#print axioms same_time_chronoid_identity
#print axioms endpoint_collapse_impossible
#print axioms meeting_boundaries_distinct
#print axioms three_coincident_boundaries_impossible
#print axioms owl_reduct_sound
#print axioms possible_projection
#print axioms certain_projection
end TemporalIntegration
