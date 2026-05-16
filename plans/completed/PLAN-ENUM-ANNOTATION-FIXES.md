# Plan: Enum annotation migration fixes

Status: **completed** — shipped (`strict=True` in `graph_enrich.py`; README enum stubs without invalid `@Target` on enums).

Companion fixes for commit `01ff39e1` ("from string to enums for java annotations").

---

## Issue index

| # | File | Severity | Description |
|---|------|----------|-------------|
| 1 | `graph_enrich.py` | Medium | `strict=False` in zip may mask misaligned tuples |
| 2 | `README.md` | Low | Invalid Java — enum declarations have inapplicable annotations |

---

## Fix 1 — Use `strict=True` in zip for parallel capability tuples

**Problem.** In `graph_enrich.py`, the zip over `container_capability_values`
and `container_capability_kinds` uses `strict=False`:

```python
for v, vk in zip(
    ann.container_capability_values,
    ann.container_capability_kinds,
    strict=False,
):
```

These two tuples are populated in lockstep by `_codebase_capability_values_from_array`
in `ast_java.py` — they should always have identical lengths. Using `strict=False`
silently truncates if they ever diverge, masking a bug in the parser.

**Location.** `graph_enrich.py`, inside `resolve_role_and_capabilities`,
in the `elif ann.name == "CodebaseCapabilities":` branch (around line 569).

**Fix.**

1. Change `strict=False` to `strict=True`.
2. No additional error handling needed — a `ValueError` from mismatched lengths
   is the correct signal that the parser has a bug.

**Acceptance check.** Existing tests pass; no behavioral change for correct inputs.

---

## Fix 2 — Remove invalid annotations from enum declarations in README

**Problem.** The README stub code shows `@Target` and `@Retention` annotations
on enum declarations:

```java
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
public enum CodebaseRoleKind {
    CONTROLLER, SERVICE, REPOSITORY, ...
}
```

`@Target` and `@Retention` are meta-annotations for annotation types (`@interface`),
not enums. This code will not compile.

**Location.** `README.md`, in the "Last resort — source stubs" code block
(lines ~225–235).

**Fix.**

Replace:

```java
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
public enum CodebaseRoleKind {
    CONTROLLER, SERVICE, REPOSITORY, COMPONENT, CONFIG, ENTITY, FEIGN_CLIENT, MAPPER, DTO
}

public enum CodebaseCapabilityKind {
    MESSAGE_LISTENER, MESSAGE_PRODUCER, SCHEDULED_TASK, EXCEPTION_HANDLER
}
```

With:

```java
public enum CodebaseRoleKind {
    CONTROLLER, SERVICE, REPOSITORY, COMPONENT, CONFIG, ENTITY, FEIGN_CLIENT, MAPPER, DTO
}

public enum CodebaseCapabilityKind {
    MESSAGE_LISTENER, MESSAGE_PRODUCER, SCHEDULED_TASK, EXCEPTION_HANDLER
}
```

**Acceptance check.** The stub code compiles when pasted into a Java project.

---

## Implementation checklist

- [ ] `graph_enrich.py`: change `strict=False` → `strict=True`
- [ ] `README.md`: remove `@Target` and `@Retention` from enum declarations
- [ ] Run tests to confirm no regressions
