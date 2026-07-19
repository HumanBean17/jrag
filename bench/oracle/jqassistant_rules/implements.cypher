// columns: implementer_fqn, interface_fqn
// All IMPLEMENTS edges from a concrete class to an interface.
MATCH (implementer:Class)-[:IMPLEMENTS]->(iface:Interface)
RETURN implementer.fqn AS implementer_fqn, iface.fqn AS interface_fqn
