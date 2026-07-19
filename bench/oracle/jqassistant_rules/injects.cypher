// columns: injector_fqn, injected_type_fqn
// param $service (optional): restrict to one injected-type FQN (upstream consumers of $service).
// Constructor injection: a class whose constructor takes a non-JDK type as a
// parameter (the framework-agnostic Spring DI signal per JQASSISTANT_COVERAGE).
// Collection injection (`List<Bean>`) is recovered separately via DEPENDS_ON
// from the generic Signature attribute (see worked example in the coverage doc).
MATCH (injector:Class)-[:DECLARES]->(ctor:Method {name: "<init>"})
      -[:HAS]->(p:Parameter)-[:OF_TYPE]->(injected:Type)
WHERE NOT injected.fqn STARTS WITH "java."
  AND ($service IS NULL OR injected.fqn = $service)
RETURN DISTINCT injector.fqn AS injector_fqn, injected.fqn AS injected_type_fqn
