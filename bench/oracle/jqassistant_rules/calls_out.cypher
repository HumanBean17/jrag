// param $caller: declaring-class FQN of the caller (e.g. "call.Caller")
// columns: callee_fqn
// Downstream callees — types declaring methods invoked by any method of $caller.
MATCH (caller:Type {fqn: $caller})-[:DECLARES]->(callerMethod:Method)
MATCH (callerMethod)-[:INVOKES]->(calleeMethod:Method)
MATCH (callee:Type)-[:DECLARES]->(calleeMethod)
WHERE callee.fqn <> $caller AND NOT callee.fqn STARTS WITH "java."
RETURN DISTINCT callee.fqn AS callee_fqn
