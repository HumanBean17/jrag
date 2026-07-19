// columns: implementer_fqn, interface_fqn
// param $interface (optional): restrict to one interface FQN.
// All IMPLEMENTS edges from a concrete class to an interface.
MATCH (implementer:Class)-[:IMPLEMENTS]->(iface:Interface)
WHERE $interface IS NULL OR iface.fqn = $interface
RETURN implementer.fqn AS implementer_fqn, iface.fqn AS interface_fqn
