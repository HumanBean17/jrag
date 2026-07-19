// columns: implementer_fqn, interface_fqn
// param $interface (optional): restrict to one interface FQN.
// All IMPLEMENTS edges from a non-interface type to its interface. jqassistant
// labels generic/some interfaces as :Type without the :Interface marker, so we
// match on the :Type label and IMPLEMENTS edge (not the strict :Class/:Interface
// labels) and exclude interfaces themselves from the implementer side.
MATCH (implementer:Type)-[:IMPLEMENTS]->(iface:Type)
WHERE ($interface IS NULL OR iface.fqn = $interface)
  AND NOT implementer:Interface
RETURN DISTINCT implementer.fqn AS implementer_fqn, iface.fqn AS interface_fqn
