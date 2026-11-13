- Whenever the answer reports a number of items used after any filtering,
  screening or selection step, it must also state the number considered
  before that step. Reporting only the surviving count fails this criterion.
- A screening or selection criterion must not be measured on the same
  quantity over a time interval that overlaps the interval the answer reports
  as its result. If the screening interval does overlap the reported
  interval, the answer must say so and state that the result is constrained
  by the selection.
- Any count, total or fraction stated anywhere in the answer -- in prose, in
  a summary, or in a table -- must either appear in a tool result shown in
  this transcript or be recomputed before it is stated. A figure with no
  computation behind it fails this criterion even when it is correct and even
  when nothing else in the answer contradicts it.
- Aggregate figures must be robust to composition. When the answer gives both
  an aggregate for a quantity (a basin/regional mean, a total-curve change,
  or a trend from a group average) AND a per-unit figure for the same
  quantity (a per-well/per-item median or typical value), the aggregate must
  not be substantially LARGER than the per-unit median -- a monotonic average
  over many units cannot exceed the typical unit that way. If it does, or if
  the aggregate/trend is the change or slope of a group mean computed over a
  set whose membership changes across the window (items entering or leaving
  the record), treat the aggregate as a likely composition artifact: flag it
  and prefer the per-unit (fixed-panel or per-item median) estimate as the
  headline. This fails even when each number is individually sourced.
- When a number introduces or summarises a list of named items ("the four
  sites -- A, B, C, D, E"), count the named items. If the count and the list
  disagree, this fails, even if the number appears correctly elsewhere.
- A quantity that an earlier answer in this session already defined or
  tabulated must keep that definition, scale and value. A count an earlier
  table gives as 18 must not reappear as 15, and a score defined earlier as
  0 or 1 must not reappear as a raw count. If this answer measures it a
  different way, it must say how and why the result differs. Earlier answers'
  wording is not evidence; only their tables and computed output are.
- For any claim that items are highest, lowest, top-N, larger than another
  item, or hold "in every" case, check each named item against the table
  column the claim refers to. "Top 10 in every scenario" fails if any named
  item falls outside the top 10 in any column, and the correction must fix
  every contradicted item, not just the first.
- When the question specifies how something is to be computed -- weights,
  thresholds, scaling, how missing values are handled, how items are grouped
  or cut -- check the code in the transcript against that specification. A
  computation that departs from it fails, unless the answer states the
  departure and why. "Spread the weight in proportion" fails if the code
  splits it 55/45 where proportion gives 58/42.
