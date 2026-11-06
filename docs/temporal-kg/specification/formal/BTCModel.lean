import SULOTemporal
set_option warningAsError true

/-! A constructed model of BTC, and SULO interpretations over it.

Chronoids are open rational intervals and boundaries are rational positions with
an orientation: the start of every chronoid that begins at q, or the end of every
chronoid that ends at q. Coincidence is equality of position, so each position
has exactly two boundaries. The model shows that the BTC axioms of
IntegratedSemantics.lean are jointly satisfiable, together with the SULO
conditions of SULOTemporal.lean, and gives the countermodels for P7 and P8 of
docs/temporal-kg/sulo-temporal-semantics.md. -/
namespace BTCModel
open TemporalIntegration SULOTemporal

/-- An open interval (lo, hi) of the rationals. -/
structure Chron where
  lo : Rat
  hi : Rat
  lt : lo < hi

theorem Chron.ext {c d : Chron} (h₁ : c.lo = d.lo) (h₂ : c.hi = d.hi) : c = d := by
  cases c
  cases d
  simp_all

/-- `true` orients a boundary as a start, `false` as an end. -/
abbrev Bound := Rat × Bool

def sig : TemporalSignature Chron Bound where
  first c := (c.lo, true)
  last c := (c.hi, false)
  part c d := d.lo ≤ c.lo ∧ c.hi ≤ d.hi
  coinc a b := a.1 = b.1

theorem part_iff {c d : Chron} : sig.part c d ↔ d.lo ≤ c.lo ∧ c.hi ≤ d.hi := Iff.rfl
theorem coinc_iff {a b : Bound} : sig.coinc a b ↔ a.1 = b.1 := Iff.rfl
theorem first_eq {c d : Chron} : sig.first c = sig.first d ↔ c.lo = d.lo := by simp [sig]
theorem last_eq {c d : Chron} : sig.last c = sig.last d ↔ c.hi = d.hi := by simp [sig]
theorem first_fst (c : Chron) : (sig.first c).1 = c.lo := rfl
theorem last_fst (c : Chron) : (sig.last c).1 = c.hi := rfl

theorem ne_of_lo {c d : Chron} (h : c.lo ≠ d.lo) : c ≠ d := fun e => h (e ▸ rfl)
theorem ne_of_hi {c d : Chron} (h : c.hi ≠ d.hi) : c ≠ d := fun e => h (e ▸ rfl)

/-- Two chronoids overlap exactly when their open intervals intersect. -/
theorem overlap_iff {c d : Chron} : sig.Overlap c d ↔ c.lo < d.hi ∧ d.lo < c.hi := by
  constructor
  · rintro ⟨a, ⟨h₁, h₂⟩, h₃, h₄⟩
    have := a.lt
    constructor <;> grind
  · rintro ⟨h₁, h₂⟩
    have := c.lt
    have := d.lt
    by_cases l : c.lo ≤ d.lo <;> by_cases h : c.hi ≤ d.hi
    · exact ⟨⟨d.lo, c.hi, h₂⟩, ⟨l, Rat.le_refl⟩, Rat.le_refl, h⟩
    · exact ⟨⟨d.lo, d.hi, d.lt⟩, ⟨l, by grind⟩, Rat.le_refl, Rat.le_refl⟩
    · exact ⟨⟨c.lo, c.hi, c.lt⟩, ⟨Rat.le_refl, Rat.le_refl⟩, by grind, h⟩
    · exact ⟨⟨c.lo, d.hi, h₁⟩, ⟨Rat.le_refl, by grind⟩, by grind, Rat.le_refl⟩

/-- In the model, BTC's boundary order is the order of positions. -/
theorem before_iff {a b : Bound} : sig.Before a b ↔ a.1 < b.1 := by
  constructor
  · rintro ⟨c, h₁, h₂⟩
    rw [coinc_iff, first_fst] at h₁
    rw [coinc_iff, last_fst] at h₂
    rw [h₁, h₂]
    exact c.lt
  · intro h
    exact ⟨⟨a.1, b.1, h⟩, rfl, rfl⟩

theorem btc : BTC sig where
  nonempty := ⟨⟨0, 1, by decide⟩⟩
  compatible c d := by
    have := c.lt
    have := d.lt
    by_cases l : c.lo ≤ d.lo <;> by_cases h : c.hi ≤ d.hi
    · exact ⟨⟨c.lo, d.hi, by grind⟩, ⟨Rat.le_refl, h⟩, l, Rat.le_refl⟩
    · exact ⟨⟨c.lo, c.hi, c.lt⟩, ⟨Rat.le_refl, Rat.le_refl⟩, l, by grind⟩
    · exact ⟨⟨d.lo, d.hi, d.lt⟩, ⟨by grind, h⟩, Rat.le_refl, Rat.le_refl⟩
    · exact ⟨⟨d.lo, c.hi, by grind⟩, ⟨by grind, Rat.le_refl⟩, Rat.le_refl, by grind⟩
  part_refl _ := ⟨Rat.le_refl, Rat.le_refl⟩
  part_antisymm c d h₁ h₂ := by
    rw [part_iff] at h₁ h₂
    exact Chron.ext (by grind) (by grind)
  part_trans c d e h₁ h₂ := by
    rw [part_iff] at *
    constructor <;> grind
  future c := by
    have := c.lt
    exact ⟨⟨c.lo, c.hi + 1, by grind⟩, ⟨⟨Rat.le_refl, by grind⟩, ne_of_hi (by simp; grind)⟩, rfl⟩
  past c := by
    have := c.lt
    exact ⟨⟨c.lo - 1, c.hi, by grind⟩, ⟨⟨by grind, Rat.le_refl⟩, ne_of_lo (by simp; grind)⟩, rfl⟩
  inner c := by
    have := c.lt
    refine ⟨⟨(2 * c.lo + c.hi) / 3, (c.lo + 2 * c.hi) / 3, by grind⟩,
      ⟨⟨by simp; grind, by simp; grind⟩, ne_of_lo (by simp; grind)⟩, ?_, ?_⟩
    · rintro ⟨_, h⟩
      rw [first_eq] at h
      simp at h
      grind
    · rintro ⟨_, h⟩
      rw [last_eq] at h
      simp at h
      grind
  dependent b := by
    rcases b with ⟨q, _ | _⟩
    · exact ⟨⟨q - 1, q, by grind⟩, Or.inr rfl⟩
    · exact ⟨⟨q, q + 1, by grind⟩, Or.inl rfl⟩
  coinc_refl _ := rfl
  coinc_symm _ _ h := h.symm
  coinc_trans _ _ _ h₁ h₂ := h₁.trans h₂
  counterpart a := by
    rcases a with ⟨q, o⟩
    exact ⟨(q, !o), by cases o <;> simp, rfl⟩
  at_most_two a b c h₁ h₂ := by
    rcases a with ⟨p, _ | _⟩ <;> rcases b with ⟨q, _ | _⟩ <;> rcases c with ⟨r, _ | _⟩ <;>
      simp_all [coinc_iff]
  intersection c d h := by
    rw [overlap_iff] at h
    have := c.lt
    have := d.lt
    by_cases l : c.lo ≤ d.lo <;> by_cases u : c.hi ≤ d.hi
    · refine ⟨⟨d.lo, c.hi, by grind⟩, ⟨l, Rat.le_refl⟩, ⟨Rat.le_refl, u⟩, ?_⟩
      intro a ha hb
      rw [part_iff] at *
      constructor <;> grind
    · refine ⟨d, ⟨l, by grind⟩, ⟨Rat.le_refl, Rat.le_refl⟩, ?_⟩
      intro a _ hb
      exact hb
    · refine ⟨c, ⟨Rat.le_refl, Rat.le_refl⟩, ⟨by grind, u⟩, ?_⟩
      intro a ha _
      exact ha
    · refine ⟨⟨c.lo, d.hi, by grind⟩, ⟨Rat.le_refl, by grind⟩, ⟨by grind, Rat.le_refl⟩, ?_⟩
      intro a ha hb
      rw [part_iff] at *
      constructor <;> grind
  supplement c d h := by
    rw [part_iff] at h
    have := c.lt
    have := d.lt
    by_cases l : d.lo ≤ c.lo
    · have u : d.hi < c.hi := by grind
      by_cases m : c.lo ≤ d.hi
      · refine ⟨⟨d.hi, c.hi, u⟩, ⟨m, Rat.le_refl⟩, ?_⟩
        rw [overlap_iff]
        grind
      · refine ⟨c, ⟨Rat.le_refl, Rat.le_refl⟩, ?_⟩
        rw [overlap_iff]
        grind
    · by_cases m : d.lo ≤ c.hi
      · refine ⟨⟨c.lo, d.lo, by grind⟩, ⟨Rat.le_refl, m⟩, ?_⟩
        rw [overlap_iff]
        grind
      · refine ⟨c, ⟨Rat.le_refl, Rat.le_refl⟩, ?_⟩
        rw [overlap_iff]
        grind
  extensional c d h := Chron.ext h.1 h.2
  proper c h := by
    have := c.lt
    rw [coinc_iff, first_fst, last_fst] at h
    grind
  span a b h := by
    rw [coinc_iff] at h
    by_cases l : a.1 < b.1
    · exact ⟨⟨a.1, b.1, l⟩, Or.inl ⟨rfl, rfl⟩⟩
    · exact ⟨⟨b.1, a.1, by grind⟩, Or.inr ⟨rfl, rfl⟩⟩
  first_nested c d h := by
    rw [coinc_iff, first_fst, first_fst] at h
    by_cases u : c.hi ≤ d.hi
    · exact Or.inl ⟨by grind, u⟩
    · exact Or.inr ⟨by grind, by grind⟩
  last_nested c d h := by
    rw [coinc_iff, last_fst, last_fst] at h
    by_cases l : d.lo ≤ c.lo
    · exact Or.inl ⟨l, by grind⟩
    · exact Or.inr ⟨by grind, by grind⟩
  embedded c d := by
    rintro ⟨a, ha₁, ha₂⟩ ⟨b, hb₁, hb₂⟩
    rw [first_eq] at ha₁
    rw [last_eq] at hb₂
    rw [coinc_iff, last_fst, first_fst] at ha₂
    rw [coinc_iff, first_fst, last_fst] at hb₁
    have := a.lt
    have := b.lt
    refine ⟨⟨⟨by grind, by grind⟩, ne_of_lo (by grind)⟩, ?_, ?_⟩
    · rintro ⟨_, h⟩
      rw [first_eq] at h
      grind
    · rintro ⟨_, h⟩
      rw [last_eq] at h
      grind
  start_fragment c d h hne := by
    rw [Ne, first_eq] at hne
    rw [part_iff] at h
    have := c.lt
    exact ⟨⟨d.lo, c.lo, by grind⟩, ⟨⟨⟨Rat.le_refl, by grind⟩, ne_of_hi (by simp; grind)⟩, rfl⟩, rfl⟩
  end_fragment c d h hne := by
    rw [Ne, last_eq] at hne
    rw [part_iff] at h
    have := c.lt
    exact ⟨⟨c.hi, d.hi, by grind⟩, ⟨⟨⟨by grind, Rat.le_refl⟩, ne_of_lo (by simp; grind)⟩, rfl⟩, rfl⟩
  meeting_sum c d h := by
    rw [TemporalSignature.Meets, coinc_iff, last_fst, first_fst] at h
    have := c.lt
    have := d.lt
    refine ⟨⟨c.lo, d.hi, by grind⟩, rfl, rfl, ?_⟩
    rintro ⟨a, ha, hc, hd⟩
    rw [part_iff] at ha
    rw [overlap_iff] at hc hd
    have := a.lt
    grind
  overlap_sum c d h := by
    rintro ⟨a, ⟨_, ha⟩, hno⟩
    rw [first_eq] at ha
    rw [overlap_iff] at h hno
    have := a.lt
    have := c.lt
    have := d.lt
    refine ⟨⟨c.lo, d.hi, by grind⟩, rfl, rfl, ?_⟩
    rintro ⟨x, hx, hc, hd⟩
    rw [part_iff] at hx
    rw [overlap_iff] at hc hd
    have := x.lt
    grind

open Classical in
/-- The integer chart: a boundary at an integer position has that integer as its
coordinate, and every other boundary is uncharted. -/
noncomputable def coord (b : Bound) : Option Int :=
  if h : ∃ n : Int, (n : Rat) = b.1 then some (Classical.choose h) else none

theorem coord_spec {b : Bound} {x : Int} (h : coord b = some x) : (x : Rat) = b.1 := by
  unfold coord at h
  split at h
  · rename_i e
    cases h
    exact Classical.choose_spec e
  · cases h

theorem coord_of_eq (n : Int) (q : Rat) (o : Bool) (hq : (n : Rat) = q) : coord (q, o) = some n := by
  unfold coord
  split
  · rename_i e
    exact congrArg some (Rat.intCast_inj.mp ((Classical.choose_spec e).trans hq.symm))
  · rename_i ne
    exact absurd ⟨n, hq⟩ ne

theorem coord_eq {n : Int} {q : Rat} {o : Bool} {x : Int} (hq : (n : Rat) = q)
    (h : coord (q, o) = some x) : x = n := by
  rw [coord_of_eq n q o hq] at h
  exact (Option.some.inj h).symm

noncomputable def chart : Chart sig where
  coord := coord
  order a b x y h hx hy := by
    rw [before_iff, ← coord_spec hx, ← coord_spec hy] at h
    exact Rat.intCast_lt_intCast.mp h
  coinc a b x y h hx hy := by
    rw [coinc_iff, ← coord_spec hx, ← coord_spec hy] at h
    exact Rat.intCast_inj.mp h

/-- S8 read off occurrences: the precedence relation an interpretation must have. -/
def precedesFrom {O : Type} (process : O → Prop) (occ : O → Option Chron) (x y : O) : Prop :=
  process x ∧ process y ∧ ∃ cx cy, occ x = some cx ∧ occ y = some cy ∧ cx.hi < cy.lo

theorem precedesFrom_iff {O : Type} {process : O → Prop} {occ : O → Option Chron}
    {a b : O} {ca cb : Chron} (pa : process a) (pb : process b)
    (oa : occ a = some ca) (ob : occ b = some cb) :
    precedesFrom process occ a b ↔ sig.Before (sig.last ca) (sig.first cb) := by
  rw [before_iff]
  constructor
  · rintro ⟨_, _, cx, cy, hx, hy, h⟩
    rw [oa] at hx
    rw [ob] at hy
    cases hx
    cases hy
    exact h
  · intro h
    exact ⟨pa, pb, ca, cb, oa, ob, h⟩

/-! ### P7: a duration filler fixes length, not position

Process `a` is described only by a duration `da` of 10; process `b` occupies the
interval `ib` = (0, 10), described by start `sb`, end `eb` and duration part `db`.
For every position `s`, placing `a` at (s, s + 10) gives a valued interpretation. -/
namespace P7
inductive Ind | a | b | da | ib | sb | eb | db

def occA (s : Rat) : Chron := ⟨s, s + 10, by grind⟩
def occB : Chron := ⟨0, 10, by decide⟩

def occ (s : Rat) : Ind → Option Chron
  | .a => some (occA s)
  | .b => some occB
  | _ => none

def process (x : Ind) : Prop := x = .a ∨ x = .b

def view (s : Rat) : SULOView Ind where
  process := process
  timeInstant x := x = .sb ∨ x = .eb
  startTime x := x = .sb
  endTime x := x = .eb
  timeInterval x := x = .ib
  duration x := x = .da ∨ x = .db
  hasPart x y := x = .ib ∧ (y = .sb ∨ y = .eb ∨ y = .db)
  hasDirectPart x y := x = .ib ∧ (y = .sb ∨ y = .eb ∨ y = .db)
  atTime x y := (x = .a ∧ y = .da) ∨ (x = .b ∧ y = .ib)
  precedes := precedesFrom process (occ s)

def values : Values Ind where
  value | .sb => some 0 | .eb => some 10 | _ => none
  isExact x := x = .sb ∨ x = .eb
  length | .da => some 10 | .db => some 10 | _ => none

theorem axioms (s : Rat) : SULOAxioms (view s) where
  start_instant _ h := Or.inl h
  end_instant _ h := Or.inr h
  start_end_disjoint := by
    rintro o ⟨h₁, h₂⟩
    exact Ind.noConfusion (h₁.symm.trans h₂)
  precedes_typed _ _ h := ⟨h.1, h.2.1⟩

noncomputable def interp (s : Rat) : ValuedInterpretation sig (view s) values where
  refC | .ib => some occB | _ => none
  refB | .sb => some (0, true) | .eb => some (10, false) | _ => none
  occ := occ s
  start_boundary := by
    rintro i p c rfl - rfl hr
    cases hr
    rfl
  end_boundary := by
    rintro i p c rfl - rfl hr
    cases hr
    rfl
  occurs_at := by
    rintro x i c px (⟨-, h⟩ | ⟨rfl, -⟩) rfl hr
    · exact Ind.noConfusion h
    · cases hr
      rfl
  precedes_iff _ _ _ _ pa pb oa ob := precedesFrom_iff pa pb oa ob
  chart := chart
  instant_value := by
    rintro o v - (rfl | rfl) hv <;> cases hv
    · exact ⟨_, rfl, coord_of_eq 0 0 true (by decide)⟩
    · exact ⟨_, rfl, coord_of_eq 10 10 false (by decide)⟩
  duration_length := by
    rintro i d c a x y rfl ⟨-, hd⟩ (rfl | rfl) hr hl hx hy
    · rcases hd with h | h | h <;> exact Ind.noConfusion h
    · cases hr
      cases hl
      rw [coord_eq (n := 0) (q := 0) (o := true) (by decide) hx,
        coord_eq (n := 10) (q := 10) (o := false) (by decide) hy]
      decide
end P7

/-- P7. For every position s, process `a`, described only by a duration filler
of 10, may occupy (s, s + 10) in a valued interpretation over BTC. -/
theorem duration_fixes_length (s : Rat) :
    BTC sig ∧ SULOAxioms (P7.view s) ∧ (P7.view s).atTime .a .da ∧ (P7.view s).duration .da ∧
    (P7.interp s).occ .a = some (P7.occA s) ∧ (P7.occA s).hi - (P7.occA s).lo = 10 :=
  ⟨btc, P7.axioms s, Or.inl ⟨rfl, rfl⟩, Or.inl rfl, rfl, by simp [P7.occA]; grind⟩

/-- P7. The duration entails no precedence, meeting or overlap between `a` and
`b` in either direction: each fails at s = 20 or at s = -20. -/
theorem duration_entails_no_relation :
    ¬(P7.view 20).precedes .a .b ∧ ¬(P7.view (-20)).precedes .b .a ∧
    ¬sig.Meets (P7.occA 20) P7.occB ∧ ¬sig.Meets P7.occB (P7.occA 20) ∧
    ¬sig.Overlap (P7.occA 20) P7.occB := by
  refine ⟨?_, ?_, ?_, ?_, ?_⟩
  · intro h
    have := (precedesFrom_iff (process := P7.process) (occ := P7.occ 20) (Or.inl rfl) (Or.inr rfl) rfl rfl).mp h
    rw [before_iff] at this
    simp [sig, P7.occA, P7.occB] at this <;> grind
  · intro h
    have := (precedesFrom_iff (process := P7.process) (occ := P7.occ (-20)) (Or.inr rfl) (Or.inl rfl) rfl rfl).mp h
    rw [before_iff] at this
    simp [sig, P7.occA, P7.occB] at this <;> grind
  · intro h
    simp [TemporalSignature.Meets, sig, P7.occA, P7.occB] at h <;> grind
  · intro h
    simp [TemporalSignature.Meets, sig, P7.occA, P7.occB] at h <;> grind
  · rw [overlap_iff]
    simp [P7.occA, P7.occB] <;> grind

/-! ### P8: ordered anchors do not establish precedence

Processes `a` and `b` are described only by instants `ta` and `tb`, which the
profile reads as the start boundaries of their occurrences (one of the S6
readings for instant fillers). The anchors are ordered, 0 before 5, and exact on
the integer chart, but the occurrences (0, 10) and (5, 15) overlap. -/
namespace P8
inductive Ind | a | b | ta | tb

def occA : Chron := ⟨0, 10, by decide⟩
def occB : Chron := ⟨5, 15, by decide⟩

def occ : Ind → Option Chron
  | .a => some occA
  | .b => some occB
  | _ => none

def process (x : Ind) : Prop := x = .a ∨ x = .b

def view : SULOView Ind where
  process := process
  timeInstant x := x = .ta ∨ x = .tb
  startTime _ := False
  endTime _ := False
  timeInterval _ := False
  duration _ := False
  hasPart _ _ := False
  hasDirectPart _ _ := False
  atTime x y := (x = .a ∧ y = .ta) ∨ (x = .b ∧ y = .tb)
  precedes := precedesFrom process occ

def values : Values Ind where
  value | .ta => some 0 | .tb => some 5 | _ => none
  isExact x := x = .ta ∨ x = .tb
  length _ := none

theorem axioms : SULOAxioms view where
  start_instant _ h := h.elim
  end_instant _ h := h.elim
  start_end_disjoint _ h := h.1.elim
  precedes_typed _ _ h := ⟨h.1, h.2.1⟩

noncomputable def interp : ValuedInterpretation sig view values where
  refC _ := none
  refB | .ta => some (0, true) | .tb => some (5, true) | _ => none
  occ := occ
  start_boundary _ _ _ h := h.elim
  end_boundary _ _ _ h := h.elim
  occurs_at _ _ _ _ _ h := h.elim
  precedes_iff _ _ _ _ pa pb oa ob := precedesFrom_iff pa pb oa ob
  chart := chart
  instant_value := by
    rintro o v - (rfl | rfl) hv <;> cases hv
    · exact ⟨_, rfl, coord_of_eq 0 0 true (by decide)⟩
    · exact ⟨_, rfl, coord_of_eq 5 5 true (by decide)⟩
  duration_length _ _ _ _ _ _ _ _ h := h.elim
end P8

/-- P8. Instant descriptions anchoring the starts of two occurrences, with the
first anchor before the second, do not entail precedence. -/
theorem anchors_do_not_give_precedence :
    BTC sig ∧ SULOAxioms P8.view ∧ P8.view.atTime .a .ta ∧ P8.view.atTime .b .tb ∧
    P8.interp.occ .a = some P8.occA ∧ P8.interp.refB .ta = some (sig.first P8.occA) ∧
    P8.interp.occ .b = some P8.occB ∧ P8.interp.refB .tb = some (sig.first P8.occB) ∧
    sig.Before (sig.first P8.occA) (sig.first P8.occB) ∧ ¬P8.view.precedes .a .b := by
  refine ⟨btc, P8.axioms, Or.inl ⟨rfl, rfl⟩, Or.inr ⟨rfl, rfl⟩, rfl, rfl, rfl, rfl, ?_, ?_⟩
  · rw [before_iff]
    decide
  · intro h
    have := (precedesFrom_iff (process := P8.process) (occ := P8.occ) (Or.inl rfl) (Or.inr rfl) rfl rfl).mp h
    rw [before_iff] at this
    simp [sig, P8.occA, P8.occB] at this <;> grind

#print axioms btc
#print axioms chart
#print axioms duration_fixes_length
#print axioms duration_entails_no_relation
#print axioms anchors_do_not_give_precedence
end BTCModel
