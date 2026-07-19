// param $seed: FQN of the changed type (e.g. "blast.C")
// columns: impacted_fqn
// Blast radius to depth 2: types that depend on $seed (directly or one hop
// further), excluding JDK types and the seed itself.
MATCH (seed:Type {fqn: $seed})
MATCH (impacted:Type)-[:DEPENDS_ON*1..2]->(seed)
WHERE impacted.fqn <> $seed AND NOT impacted.fqn STARTS WITH "java."
RETURN DISTINCT impacted.fqn AS impacted_fqn
