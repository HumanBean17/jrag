// param $callee: declaring-class FQN of the callee (e.g. "call.Callee")
// columns: caller_fqn
// Upstream callers — types whose methods invoke any method declared by $callee.
MATCH (callee:Type {fqn: $callee})-[:DECLARES]->(calleeMethod:Method)
MATCH (callerMethod:Method)-[:INVOKES]->(calleeMethod)
MATCH (caller:Type)-[:DECLARES]->(callerMethod)
WHERE caller.fqn <> $callee AND NOT caller.fqn STARTS WITH "java."
RETURN DISTINCT caller.fqn AS caller_fqn
