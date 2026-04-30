# Enhanced Role Recognition: Supertype and Name-Based Detection

## Problem Statement

Current role recognition relies primarily on Spring stereotype annotations (`@Service`, `@Repository`, `@Controller`, etc.). This creates blind spots for:

1. **Spring Data repositories** - They extend `JpaRepository`/`CrudRepository` without needing `@Repository` annotation (Spring auto-discovers them)
2. **Legacy codebases** - Older code following naming conventions but lacking annotations
3. **Non-Spring frameworks** - Projects using other DI frameworks or no framework at all

### Evidence from current index:

```
AssignSplitRepository extends JpaRepository → role: "OTHER"  ❌
AssignOperatorSplitRepository extends JpaRepository → role: "OTHER"  ❌
ChatSessionRepository extends JpaRepository → role: "OTHER"  ❌
```

These are clearly repositories but get misclassified.

## Proposed Solution

Add two new detection layers with clear priority ordering:

### Priority Order (highest to lowest)

1. **Annotation-based** (existing) - `@Service` → SERVICE
2. **Supertype-based** (new) - `extends JpaRepository` → REPOSITORY
3. **Name suffix-based** (new) - `*Service` → SERVICE
4. **DTO detection** (existing) - records, Lombok, DTO suffixes

Annotation always wins — if a class has `@Service` but is named `FooRepository`, it stays SERVICE.

## Implementation Details

### 1. Supertype-to-Role Mapping

High confidence detection based on interface/class hierarchy:

```python
_SUPERTYPE_TO_ROLE: dict[str, str] = {
    # Spring Data JPA
    "JpaRepository": "REPOSITORY",
    "CrudRepository": "REPOSITORY",
    "PagingAndSortingRepository": "REPOSITORY",
    "JpaSpecificationExecutor": "REPOSITORY",
    
    # Spring Data - other stores
    "MongoRepository": "REPOSITORY",
    "ReactiveMongoRepository": "REPOSITORY",
    "ElasticsearchRepository": "REPOSITORY",
    "R2dbcRepository": "REPOSITORY",
    "ReactiveCrudRepository": "REPOSITORY",
    "KeyValueRepository": "REPOSITORY",
    
    # MyBatis
    "BaseMapper": "MAPPER",
}
```

### 2. Name Suffix-to-Role Mapping

Medium confidence detection based on class naming conventions:

```python
_NAME_SUFFIX_TO_ROLE: dict[str, str] = {
    # Repository layer
    "Repository": "REPOSITORY",
    "RepositoryImpl": "REPOSITORY",
    "Dao": "REPOSITORY",
    "DaoImpl": "REPOSITORY",
    
    # Service layer
    "Service": "SERVICE",
    "ServiceImpl": "SERVICE",
    "Facade": "SERVICE",
    "FacadeImpl": "SERVICE",
    
    # Controller layer
    "Controller": "CONTROLLER",
    "ControllerImpl": "CONTROLLER",
    "RestController": "CONTROLLER",
    "Resource": "CONTROLLER",  # JAX-RS style
    
    # Mapper layer
    "Mapper": "MAPPER",
    "MapperImpl": "MAPPER",
    
    # Integration layer
    "Client": "FEIGN_CLIENT",
    "FeignClient": "FEIGN_CLIENT",
    
    # Configuration
    "Config": "CONFIG",
    "Configuration": "CONFIG",
    "Properties": "CONFIG",
}
```

### 3. Modified `infer_role_for_type` Function

```python
def infer_role_for_type(type_decl: TypeDecl) -> str:
    """Role inference with multi-signal detection.
    
    Priority: annotation > supertype > name suffix > DTO heuristics
    """
    # 1. Annotation-based (existing, highest priority)
    ann_names = [a.name for a in type_decl.annotations]
    base = infer_role(ann_names)
    if base != "OTHER":
        return base
    
    # 2. Supertype-based (new, high confidence)
    for sup in (*type_decl.extends, *type_decl.implements):
        role = _SUPERTYPE_TO_ROLE.get(sup)
        if role:
            return role
    
    # 3. Name suffix-based (new, medium confidence)
    name = type_decl.name or ""
    for suffix, role in _NAME_SUFFIX_TO_ROLE.items():
        if name.endswith(suffix) and len(name) > len(suffix):
            return role
    
    # 4. DTO detection (existing)
    if type_decl.kind == "record":
        return "DTO"
    
    ann_set = set(ann_names)
    if ann_set & _DTO_LOMBOK_ANNOTATIONS:
        return "DTO"
    
    for suffix in _DTO_NAME_SUFFIXES:
        if name.endswith(suffix) and name != suffix:
            return "DTO"
    
    return "OTHER"
```

## Negative Patterns (Exclusions)

To avoid false positives, these patterns should NOT trigger role assignment:

| Pattern | Reason |
|---------|--------|
| `*RepositoryTest` | Test class |
| `*ServiceTest` | Test class |
| `*Mock*` | Test infrastructure |
| `*Stub*` | Test infrastructure |
| `*ControllerAdvice` | Has own annotation handling |
| `Abstract*` | Base classes, not concrete roles |

Implementation approach:
```python
_ROLE_EXCLUSION_PATTERNS: tuple[str, ...] = (
    "Test", "Tests", "Mock", "Stub", "Fake", "Spy",
)

def _is_test_or_mock(name: str) -> bool:
    return any(p in name for p in _ROLE_EXCLUSION_PATTERNS)
```

## Future Enhancements

### Package Path as Weak Signal (Optional)

```python
_PACKAGE_SEGMENT_TO_ROLE: dict[str, str] = {
    "repository": "REPOSITORY",
    "repositories": "REPOSITORY",
    "repo": "REPOSITORY",
    "service": "SERVICE",
    "services": "SERVICE",
    "controller": "CONTROLLER",
    "controllers": "CONTROLLER",
    "web": "CONTROLLER",
    "api": "CONTROLLER",
    "rest": "CONTROLLER",
    "config": "CONFIG",
    "configuration": "CONFIG",
}
```

This could serve as tertiary signal when all else fails.

### New Roles for Modern Patterns

| Role | Patterns | Use Case |
|------|----------|----------|
| `PORT` | `*Port`, `*Gateway` | Hexagonal architecture |
| `ADAPTER` | `*Adapter` | Hexagonal architecture |
| `HANDLER` | `*Handler`, `*CommandHandler`, `*QueryHandler` | CQRS |
| `LISTENER` | `*Listener`, `*EventHandler` | Event-driven |
| `FACTORY` | `*Factory` | Creational patterns |
| `PROVIDER` | `*Provider` | Dependency provision |

## Impact Analysis

### Files to Modify

1. `ast_java.py` - Add new mappings and modify `infer_role_for_type`
2. `java_ontology.py` - Update `VALID_ROLES` if new roles added
3. Tests - Add test cases for new detection logic

### Backward Compatibility

- Annotation-based detection unchanged (highest priority)
- Existing DTO detection unchanged
- Only previously `OTHER` classes may get reclassified
- No breaking changes to existing roles

### Expected Improvements

| Before | After |
|--------|-------|
| `*Repository extends JpaRepository` → OTHER | → REPOSITORY |
| `*Service` (no annotation) → OTHER | → SERVICE |
| `*Controller` (no annotation) → OTHER | → CONTROLLER |
| `*Mapper` (no annotation) → OTHER | → MAPPER |

## Testing Strategy

1. Unit tests for each suffix pattern
2. Unit tests for supertype detection
3. Integration test with real codebase snapshot
4. Verify annotation priority preserved
5. Verify exclusion patterns work

## Rollout

1. Implement behind feature flag initially
2. Run on test codebase, compare role distribution
3. Review false positive rate
4. Adjust patterns as needed
5. Enable by default after validation
