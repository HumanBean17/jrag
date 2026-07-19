// param $seed: FQN of the changed type (e.g. "blast.C")
// columns: impacted_fqn
// Blast radius to depth 2: types that depend on $seed (directly or one hop
// further), excluding JDK types, the seed itself, and anonymous/local inner
// classes (the `$`-suffixed noise) for a clean, reviewer-readable set.
MATCH (seed:Type {fqn: $seed})
MATCH (impacted:Type)-[:DEPENDS_ON*1..2]->(seed)
WHERE impacted.fqn <> $seed
  AND NOT impacted.fqn STARTS WITH "java."
  AND NOT impacted.fqn CONTAINS "$"
RETURN DISTINCT impacted.fqn AS impacted_fqn
