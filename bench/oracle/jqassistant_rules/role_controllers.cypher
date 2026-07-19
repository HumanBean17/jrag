// columns: fqn
// param $package (optional): restrict to FQNs starting with this package prefix.
// Classes annotated @RestController / @Controller. Matched by annotation-name
// suffix so the same rule covers the fixture's local annotations and real
// Spring's org.springframework.web.bind.annotation.RestController / @Controller.
MATCH (c:Class)-[:ANNOTATED_BY]->(:Annotation)-[:OF_TYPE]->(a:Type)
WHERE (a.fqn ENDS WITH ".RestController" OR a.fqn ENDS WITH ".Controller")
  AND ($package IS NULL OR c.fqn STARTS WITH $package)
RETURN DISTINCT c.fqn AS fqn
