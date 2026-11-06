import IntegratedSemantics
set_option warningAsError true

/-! SULO 0.2.14's temporal vocabulary interpreted in BTC: conditions S1, S2, S4,
S6 and S8 of docs/temporal-kg/sulo-temporal-semantics.md, and its consequences
P1-P4 and P6.

SULO temporal individuals are descriptions. `refC` and `refB` are the partial
reference map rho (interval descriptions to chronoids, boundary descriptions to
boundaries) and `occ` the partial occurrence map. The order on boundaries is BTC's
own `TemporalSignature.Before`. Values enter through one partial chart in integer
microseconds; every statement concerns a single comparable clock. -/
namespace SULOTemporal
open TemporalIntegration
universe u v w

/-- The SULO 0.2.14 predicates the conditions mention. Satisfaction of the full
ontology is external, as for OWLView. -/
structure SULOView (O : Type w) where
  process : O → Prop
  timeInstant : O → Prop
  startTime : O → Prop
  endTime : O → Prop
  timeInterval : O → Prop
  duration : O → Prop
  hasPart : O → O → Prop
  hasDirectPart : O → O → Prop
  atTime : O → O → Prop
  precedes : O → O → Prop

/-- The SULO 0.2.14 axioms used below, as stated in sulo-0.2.14.ttl. -/
structure SULOAxioms {O : Type w} (V : SULOView O) : Prop where
  start_instant : ∀ o, V.startTime o → V.timeInstant o
  end_instant : ∀ o, V.endTime o → V.timeInstant o
  start_end_disjoint : ∀ o, ¬(V.startTime o ∧ V.endTime o)
  precedes_typed : ∀ a b, V.precedes a b → V.process a ∧ V.process b

/-- A SULO temporal interpretation: the reference and occurrence maps with
conditions S2 (oriented boundaries), S6 for interval fillers (complete
occurrence) and S8 (strict whole-interval precedence). -/
structure Interpretation {C : Type u} {B : Type v} {O : Type w}
    (t : TemporalSignature C B) (V : SULOView O) where
  refC : O → Option C
  refB : O → Option B
  occ : O → Option C
  start_boundary : ∀ i p c, V.timeInterval i → V.hasDirectPart i p → V.startTime p →
    refC i = some c → refB p = some (t.first c)
  end_boundary : ∀ i p c, V.timeInterval i → V.hasDirectPart i p → V.endTime p →
    refC i = some c → refB p = some (t.last c)
  occurs_at : ∀ x i c, V.process x → V.atTime x i → V.timeInterval i →
    refC i = some c → occ x = some c
  precedes_iff : ∀ a b ca cb, V.process a → V.process b →
    occ a = some ca → occ b = some cb →
    (V.precedes a b ↔ t.Before (t.last ca) (t.first cb))

variable {C : Type u} {B : Type v} {O : Type w}
variable {t : TemporalSignature C B} {V : SULOView O}

/-- P1. A described interval is proper: its start referent precedes its end
referent, and the two do not coincide. -/
theorem interval_proper (h : BTC t) (m : Interpretation t V)
    {i p q : O} {c : C} {bp bq : B}
    (hi : V.timeInterval i) (hp : V.hasDirectPart i p) (sp : V.startTime p)
    (hq : V.hasDirectPart i q) (eq : V.endTime q) (ri : m.refC i = some c)
    (rp : m.refB p = some bp) (rq : m.refB q = some bq) :
    t.Before bp bq ∧ ¬t.coinc bp bq := by
  have e1 : t.first c = bp := Option.some.inj ((m.start_boundary i p c hi hp sp ri).symm.trans rp)
  have e2 : t.last c = bq := Option.some.inj ((m.end_boundary i q c hi hq eq ri).symm.trans rq)
  subst e1
  subst e2
  exact ⟨first_before_last h c, h.proper c⟩

/-- P2. When one interval meets the next, the end description of the first and
the start description of the second are distinct individuals, as SULO's
StartTime/EndTime disjointness requires, and their referents coincide. -/
theorem meeting_descriptions_distinct (ax : SULOAxioms V) (m : Interpretation t V)
    {i j p q : O} {c d : C}
    (hi : V.timeInterval i) (hj : V.timeInterval j)
    (hq : V.hasDirectPart i q) (eq : V.endTime q)
    (hp : V.hasDirectPart j p) (sp : V.startTime p)
    (ri : m.refC i = some c) (rj : m.refC j = some d) (meets : t.Meets c d) :
    q ≠ p ∧ ∃ bq bp, m.refB q = some bq ∧ m.refB p = some bp ∧ t.coinc bq bp := by
  refine ⟨?_, t.last c, t.first d, m.end_boundary i q c hi hq eq ri,
    m.start_boundary j p d hj hp sp rj, meets⟩
  intro same
  subst same
  exact ax.start_end_disjoint q ⟨sp, eq⟩

/-- P3. Two interval descriptions whose start referents and end referents
coincide describe one chronoid (BTC A25). The descriptions themselves are not
equated. -/
theorem same_extent_same_chronoid (h : BTC t) (m : Interpretation t V)
    {i₁ i₂ p₁ p₂ q₁ q₂ : O} {c₁ c₂ : C} {a₁ a₂ z₁ z₂ : B}
    (hi₁ : V.timeInterval i₁) (hi₂ : V.timeInterval i₂)
    (hp₁ : V.hasDirectPart i₁ p₁) (sp₁ : V.startTime p₁)
    (hq₁ : V.hasDirectPart i₁ q₁) (eq₁ : V.endTime q₁)
    (hp₂ : V.hasDirectPart i₂ p₂) (sp₂ : V.startTime p₂)
    (hq₂ : V.hasDirectPart i₂ q₂) (eq₂ : V.endTime q₂)
    (ri₁ : m.refC i₁ = some c₁) (ri₂ : m.refC i₂ = some c₂)
    (rp₁ : m.refB p₁ = some a₁) (rp₂ : m.refB p₂ = some a₂)
    (rq₁ : m.refB q₁ = some z₁) (rq₂ : m.refB q₂ = some z₂)
    (starts : t.coinc a₁ a₂) (ends : t.coinc z₁ z₂) : c₁ = c₂ := by
  have f₁ : t.first c₁ = a₁ := Option.some.inj ((m.start_boundary i₁ p₁ c₁ hi₁ hp₁ sp₁ ri₁).symm.trans rp₁)
  have f₂ : t.first c₂ = a₂ := Option.some.inj ((m.start_boundary i₂ p₂ c₂ hi₂ hp₂ sp₂ ri₂).symm.trans rp₂)
  have l₁ : t.last c₁ = z₁ := Option.some.inj ((m.end_boundary i₁ q₁ c₁ hi₁ hq₁ eq₁ ri₁).symm.trans rq₁)
  have l₂ : t.last c₂ = z₂ := Option.some.inj ((m.end_boundary i₂ q₂ c₂ hi₂ hq₂ eq₂ ri₂).symm.trans rq₂)
  subst f₁
  subst f₂
  subst l₁
  subst l₂
  exact h.extensional c₁ c₂ ⟨starts, ends⟩

/-- P4. Under S8, precedes is irreflexive on processes with defined occurrences. -/
theorem precedes_irrefl (h : BTC t) (m : Interpretation t V)
    {a : O} {c : C} (pa : V.process a) (oa : m.occ a = some c) : ¬V.precedes a a := by
  intro self
  have back := (m.precedes_iff a a c c pa pa oa oa).mp self
  exact before_irrefl h _ (before_trans h _ _ _ (first_before_last h c) back)

/-- P4. Under S8, precedes is transitive on processes with defined occurrences. -/
theorem precedes_trans (h : BTC t) (m : Interpretation t V)
    {a b d : O} {ca cb cd : C} (pa : V.process a) (pb : V.process b) (pd : V.process d)
    (oa : m.occ a = some ca) (ob : m.occ b = some cb) (od : m.occ d = some cd)
    (hab : V.precedes a b) (hbd : V.precedes b d) : V.precedes a d := by
  have h₁ := (m.precedes_iff a b ca cb pa pb oa ob).mp hab
  have h₂ := (m.precedes_iff b d cb cd pb pd ob od).mp hbd
  exact (m.precedes_iff a d ca cd pa pd oa od).mpr
    (before_trans h _ _ _ h₁ (before_trans h _ _ _ (first_before_last h cb) h₂))

/-- P4. Under S8, precedes is asymmetric on processes with defined occurrences.
OWL 2 DL cannot declare this for a transitive property; it follows here. -/
theorem precedes_asymm (h : BTC t) (m : Interpretation t V)
    {a b : O} {ca cb : C} (pa : V.process a) (pb : V.process b)
    (oa : m.occ a = some ca) (ob : m.occ b = some cb)
    (hab : V.precedes a b) : ¬V.precedes b a := fun hba =>
  precedes_irrefl h m pa oa (precedes_trans h m pa pb pa oa ob oa hab hba)

/-- S6 with S8: precedence between processes follows from their atTime interval
descriptions alone, through the referents of those descriptions. -/
theorem precedes_from_atTime (m : Interpretation t V)
    {a b i j : O} {ci cj : C} (pa : V.process a) (pb : V.process b)
    (ai : V.atTime a i) (bj : V.atTime b j) (hi : V.timeInterval i) (hj : V.timeInterval j)
    (ri : m.refC i = some ci) (rj : m.refC j = some cj) :
    V.precedes a b ↔ t.Before (t.last ci) (t.first cj) :=
  m.precedes_iff a b ci cj pa pb (m.occurs_at a i ci pa ai hi ri) (m.occurs_at b j cj pb bj hj rj)

/-- A partial metric chart for one clock, in integer microseconds (the execution
profile's `GFOBoundaryMicrosecond`). It must be partial: BTC is dense (A12), so no
total order-preserving map into the integers exists. It is order-preserving and
constant on coincidence classes; that it reflects equality is proved below. -/
structure Chart {C : Type u} {B : Type v} (t : TemporalSignature C B) where
  coord : B → Option Int
  order : ∀ a b x y, t.Before a b → coord a = some x → coord b = some y → x < y
  coinc : ∀ a b x y, t.coinc a b → coord a = some x → coord b = some y → x = y

/-- Data a graph supplies: an instant's value on the clock, whether the admission
policy declares it exact, and a duration's value converted through its unit to
the chart's microseconds. -/
structure Values (O : Type w) where
  value : O → Option Int
  isExact : O → Prop
  length : O → Option Int

/-- An interpretation with a chart, satisfying S1 for exact values (bounded values
are §8 and are not modelled) and S4 for duration parts. -/
structure ValuedInterpretation {C : Type u} {B : Type v} {O : Type w}
    (t : TemporalSignature C B) (V : SULOView O) (g : Values O)
    extends Interpretation t V where
  chart : Chart t
  instant_value : ∀ o v, V.timeInstant o → g.isExact o → g.value o = some v →
    ∃ b, refB o = some b ∧ chart.coord b = some v
  duration_length : ∀ i d c a x y, V.timeInterval i → V.hasPart i d → V.duration d →
    refC i = some c → g.length d = some a →
    chart.coord (t.first c) = some x → chart.coord (t.last c) = some y → a = y - x

variable {g : Values O}

/-- Charts reflect equality: boundaries with one coordinate coincide (BTC A27). -/
theorem chart_reflects (h : BTC t) (k : Chart t) {a b : B} {x : Int}
    (ha : k.coord a = some x) (hb : k.coord b = some x) : t.coinc a b := by
  refine Classical.byContradiction fun distinct => ?_
  rcases before_total h a b distinct with ab | ba
  · have := k.order a b x x ab ha hb
    omega
  · have := k.order b a x x ba hb ha
    omega

/-- S1 with S2: an exact start value is the chart coordinate of the first boundary
of the interval's chronoid. -/
theorem start_coord (ax : SULOAxioms V) (m : ValuedInterpretation t V g)
    {i s : O} {c : C} {v : Int} (hi : V.timeInterval i) (hs : V.hasDirectPart i s)
    (ss : V.startTime s) (ri : m.refC i = some c) (ex : g.isExact s) (hv : g.value s = some v) :
    m.chart.coord (t.first c) = some v := by
  obtain ⟨b, rb, cb⟩ := m.instant_value s v (ax.start_instant s ss) ex hv
  have e : t.first c = b := Option.some.inj ((m.start_boundary i s c hi hs ss ri).symm.trans rb)
  rw [e]
  exact cb

/-- S1 with S2: an exact end value is the chart coordinate of the last boundary. -/
theorem end_coord (ax : SULOAxioms V) (m : ValuedInterpretation t V g)
    {i e : O} {c : C} {v : Int} (hi : V.timeInterval i) (he : V.hasDirectPart i e)
    (se : V.endTime e) (ri : m.refC i = some c) (ex : g.isExact e) (hv : g.value e = some v) :
    m.chart.coord (t.last c) = some v := by
  obtain ⟨b, rb, cb⟩ := m.instant_value e v (ax.end_instant e se) ex hv
  have eq : t.last c = b := Option.some.inj ((m.end_boundary i e c hi he se ri).symm.trans rb)
  rw [eq]
  exact cb

/-- P1 with values. Exact start and end values of an interval with a referent are
strictly ordered. -/
theorem exact_values_ordered (h : BTC t) (ax : SULOAxioms V) (m : ValuedInterpretation t V g)
    {i s e : O} {c : C} {vs ve : Int} (hi : V.timeInterval i)
    (hs : V.hasDirectPart i s) (ss : V.startTime s) (he : V.hasDirectPart i e) (se : V.endTime e)
    (ri : m.refC i = some c) (xs : g.isExact s) (vs' : g.value s = some vs)
    (xe : g.isExact e) (ve' : g.value e = some ve) : vs < ve :=
  m.chart.order _ _ vs ve (first_before_last h c)
    (start_coord ax m hi hs ss ri xs vs') (end_coord ax m hi he se ri xe ve')

/-- P6. A duration part equals the distance between exact start and end values. -/
theorem duration_agrees (ax : SULOAxioms V) (m : ValuedInterpretation t V g)
    {i s e d : O} {c : C} {vs ve a : Int} (hi : V.timeInterval i)
    (hs : V.hasDirectPart i s) (ss : V.startTime s) (he : V.hasDirectPart i e) (se : V.endTime e)
    (hd : V.hasPart i d) (dd : V.duration d) (ri : m.refC i = some c)
    (xs : g.isExact s) (vs' : g.value s = some vs) (xe : g.isExact e) (ve' : g.value e = some ve)
    (ld : g.length d = some a) : a = ve - vs :=
  m.duration_length i d c a vs ve hi hd dd ri ld
    (start_coord ax m hi hs ss ri xs vs') (end_coord ax m hi he se ri xe ve')

/-- P6. A duration that disagrees with exact endpoint values admits no valued
interpretation giving the interval a referent: it must be rejected at admission. -/
theorem duration_disagreement_inconsistent (ax : SULOAxioms V) (m : ValuedInterpretation t V g)
    {i s e d : O} {c : C} {vs ve a : Int} (hi : V.timeInterval i)
    (hs : V.hasDirectPart i s) (ss : V.startTime s) (he : V.hasDirectPart i e) (se : V.endTime e)
    (hd : V.hasPart i d) (dd : V.duration d) (ri : m.refC i = some c)
    (xs : g.isExact s) (vs' : g.value s = some vs) (xe : g.isExact e) (ve' : g.value e = some ve)
    (ld : g.length d = some a) (disagree : a ≠ ve - vs) : False :=
  disagree (duration_agrees ax m hi hs ss he se hd dd ri xs vs' xe ve' ld)

/-- P6. A duration part of an interval whose endpoints are charted is positive. -/
theorem duration_positive (h : BTC t) (m : ValuedInterpretation t V g)
    {i d : O} {c : C} {a x y : Int} (hi : V.timeInterval i) (hd : V.hasPart i d)
    (dd : V.duration d) (ri : m.refC i = some c) (ld : g.length d = some a)
    (hx : m.chart.coord (t.first c) = some x) (hy : m.chart.coord (t.last c) = some y) :
    0 < a := by
  have len := m.duration_length i d c a x y hi hd dd ri ld hx hy
  have lt := m.chart.order _ _ x y (first_before_last h c) hx hy
  omega

/-- S8 read on the chart: when the end of one occurrence and the start of the next
are charted, precedence is exactly comparison of their coordinates. -/
theorem precedes_iff_coords (h : BTC t) (m : ValuedInterpretation t V g)
    {a b : O} {ca cb : C} {x y : Int} (pa : V.process a) (pb : V.process b)
    (oa : m.occ a = some ca) (ob : m.occ b = some cb)
    (hx : m.chart.coord (t.last ca) = some x) (hy : m.chart.coord (t.first cb) = some y) :
    V.precedes a b ↔ x < y := by
  refine (m.precedes_iff a b ca cb pa pb oa ob).trans ⟨fun hb => m.chart.order _ _ x y hb hx hy, ?_⟩
  intro lt
  refine (Classical.em (t.coinc (t.last ca) (t.first cb))).elim (fun co => ?_) (fun distinct => ?_)
  · have := m.chart.coinc _ _ x y co hx hy
    omega
  · rcases before_total h _ _ distinct with ab | ba
    · exact ab
    · have := m.chart.order _ _ y x ba hy hx
      omega

#print axioms interval_proper
#print axioms meeting_descriptions_distinct
#print axioms same_extent_same_chronoid
#print axioms precedes_irrefl
#print axioms precedes_trans
#print axioms precedes_asymm
#print axioms precedes_from_atTime
#print axioms chart_reflects
#print axioms exact_values_ordered
#print axioms duration_agrees
#print axioms duration_disagreement_inconsistent
#print axioms duration_positive
#print axioms precedes_iff_coords
end SULOTemporal
