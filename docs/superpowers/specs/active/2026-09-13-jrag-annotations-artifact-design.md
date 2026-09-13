# jrag-annotations — ship the brownfield annotations as a Maven Central artifact

**Status:** implemented

## Context

The brownfield annotation surface — `@CodebaseRole`, `@CodebaseCapability`,
`@CodebaseHttpRoute`, `@CodebaseAsyncRoute`, `@CodebaseHttpClient`,
`@CodebaseProducer`, plus the `CodebaseRoleKind`, `CodebaseCapabilityKind`,
`CodebaseHttpMethod`, `CodebaseClientKind`, `CodebaseProducerKind enums —
exists only as fenced Java blocks in `docs/CONFIGURATION.md` §4.3 and as
verbatim copies under `tests/fixtures/brownfield_route_stubs/` and
`tests/fixtures/brownfield_client_stubs/`. Consumers copy-paste them into
their own package. Consequences: no compile-time checking of annotation
usage, no IDE autocomplete, onboarding friction, and a three-way drift risk
between the parser vocabulary (`ast_java.py` / `ast_kotlin.py` /
`graph_enrich.py` / `java_ontology.py`), the docs blocks, and the fixtures.
The copy-paste decision was deliberate in the original brownfield plan
("matched by simple name, no jar dependency") and predates any packaging
discussion; no spec or plan has ever tracked distribution.

The parser is already package-agnostic: it matches annotation simple names
and reads attributes at usage sites, so whether a stub definition lives in a
pasted file or arrives on the compile classpath is invisible to indexing.
All stubs are `@Retention(SOURCE)` — nothing reaches runtime bytecode.

## Decisions (settled with maintainer, 2026-09-13)

- **Distribution:** a real Maven Central artifact (not JitPack, not
  CLI-generated vendoring, not a JAR bundled in the wheel).
- **Coordinates:** `io.github.humanbean17:jrag-annotations` — the namespace
  is verifiable against the public GitHub account through the Sonatype
  Central Portal.
- **Versioning:** independent of the Python releases. The artifact gets its
  own line starting at `1.0.0` and is republished only when the annotation
  surface changes. It does **not** join the dual-PyPI same-version sync
  rule.
- **Repo shape:** a canonical, compilable Maven module (`annotations/`) in
  this repository is the single source of truth; docs and fixtures are tied
  to it by tests, not by generation.

## Goal

Consumers declare one `provided`/`compileOnly` dependency and get the full
brownfield annotation surface with compile-time checking and IDE support.
The copy-paste path remains documented and tested as the zero-dependency
alternative. No parser or CLI behavior changes.

## Non-goals

- Any change to the indexing pipeline, MCP surface, or `jrag` CLI.
- Removing or deprecating the copy-paste stub path.
- A Kotlin-specific artifact variant (the Java artifact serves `.kt` sources;
  `ast_kotlin` matches the same simple names).
- Runtime artifacts of any kind (SOURCE retention; nothing to ship at
  runtime).
- Coupling artifact releases to Python tags or the PyPI release workflow.

## Design

### Artifact contract

- **Coordinates:** `io.github.humanbean17:jrag-annotations`.
- **Initial version:** `1.0.0` — the surface has been stable since the v2
  ("Direction-Honest, Enum-Typed") redesign.
- **Scope:** `provided` (Maven) / `compileOnly` (Gradle). Because every
  annotation is SOURCE-retained, the artifact never appears in consumer
  runtime bytecode; classpath version conflicts are impossible by
  construction, and compile-time is the only compatibility point.
- **Bytecode baseline:** Java 8 (`maven.compiler.release=8`), zero
  dependencies beyond `java.lang.annotation`.
- **Contents:** the six annotations, five `@Repeatable` container annotations
  (`CodebaseCapabilities`, `CodebaseHttpRoutes`, `CodebaseAsyncRoutes`,
  `CodebaseHttpClients`, `CodebaseProducers`), and the five enums — shapes
  exactly as documented in `CONFIGURATION.md` §4.3 today. Each type carries
  minimal javadoc (one sentence + pointer to §4.3), satisfying the javadoc
  jar Central requires and powering IDE hover text.
- **POM metadata:** name, description, url, license, developer, scm — the
  fields Central validates, all derived from this repository.
- **Compatibility statement** (documented in §4.3, enforced by tests):
  annotations `1.x` vocabulary equals the jrag parser vocabulary as of jrag
  `0.12`. Vocabulary growth (a new enum constant or annotation) forces an
  annotations minor bump and an update to that line.

### The `annotations/` module

Standard Maven layout at the repository root:

```
annotations/
  pom.xml
  src/main/java/io/github/humanbean17/jrag/annotations/   (16 source files)
```

The module is the **single source of truth** for the annotation surface.
Nothing in the Python package builds or reads it at runtime; the module
exists to be compiled in CI and published on demand.

### Drift prevention — three-way consistency test

New `tests/_meta/test_annotations_module_consistency.py`, pure file reads,
runs in normal pytest with no Java toolchain (same pattern as
`test_agent_guide_consistency.py`):

1. **Module ↔ parser:** every annotation simple name declared in the module
   appears in the recognized-name constants in `ast_java.py` /
   `ast_kotlin.py` / `graph_enrich.py`, and every enum constant in the module
   is a member of the corresponding `java_ontology.py` set (`VALID_ROLES`,
   `VALID_CAPABILITIES`, `VALID_CLIENT_KINDS`, `VALID_PRODUCER_KINDS`) — and
   conversely, a parser vocabulary entry with no module source fails too.
2. **Module ↔ docs:** the fenced Java blocks in `CONFIGURATION.md` §4.3 match
   module sources after normalization (package line, imports, and javadoc
   stripped). The docs stay pedagogical but can no longer silently diverge.
3. **Module ↔ fixtures:** the `tests/fixtures/brownfield_*_stubs/` stubs keep
   their `com.example.rag` package — they are the test for the copy-in-source
   path — and the test asserts their annotation names and enum constants
   match the module's.

### Release workflow

New `.github/workflows/release-annotations.yml`, `workflow_dispatch`-only,
with a `version` input. Independent of the Python tag train by design.

1. **Idempotency guard:** fail fast if
   `io.github.humanbean17:jrag-annotations:<version>` already exists on
   Maven Central (Central versions are immutable).
2. **Build:** Temurin JDK (17, compiling `--release 8`) → `mvn -B verify` —
   compile, sources jar, javadoc jar.
3. **Publish:** GPG-sign all artifacts, upload through the Sonatype Central
   Portal publisher. Secrets (`CENTRAL_PORTAL_USERNAME`, `CENTRAL_PORTAL_TOKEN`,
   `GPG_PRIVATE_KEY`, `GPG_PASSPHRASE`) live in an environment-protected
   `release` context.
4. **Record:** push annotated tag `annotations-vX.Y.Z` (namespace disjoint
   from Python's `vX.Y.Z`) and open a GitHub Release scoped to `annotations/**`.

**One-time setup** (manual, documented in the workflow header): verify the
`io.github.humanbean17` namespace on the Central Portal, generate the GPG
signing key and publish its fingerprint to a keyserver, add the four
secrets.

### CI compile guard

`test.yml` gains an `annotations-build` job: same JDK setup, `mvn -B verify`
(seconds, no Python dependencies). Every PR touching `annotations/**` — or
the parser vocabulary, via the consistency test failing — is caught before
release day. Python jobs are untouched; local contributors never need Maven.

### Docs changes

- **`docs/CONFIGURATION.md` §4.3** — leads with the dependency snippet
  (Maven `provided` + Gradle `compileOnly` at `1.0.0`) as the primary path;
  the copy-paste blocks stay, reframed as the zero-dependency alternative;
  the compatibility line and a note that Kotlin sources use the same
  artifact.
- **`docs/CODEBASE_REQUIREMENTS.md` §A.2.1** — prefer the artifact; keep
  copy-paste as fallback.
- **`AGENTS.md` (Publishing)** — a short paragraph: the Maven artifact is a
  third, independently-versioned channel with its own manual-dispatch
  workflow and `annotations-v*` tag namespace, outside the dual-PyPI sync
  rule.

### Error handling

- Missing secrets or an existing version fail the workflow before any
  upload; no partial states on the runner side.
- Central-side validation failures (missing javadoc, POM metadata gaps)
  surface through Portal publish validation; the complete POM makes this a
  guard, not an expected path.
- A publish interrupted after upload but before Portal release is recovered
  from the Portal's held state; re-uploading over a published version is
  never attempted (the guard blocks it).

## Testing

- `tests/_meta/test_annotations_module_consistency.py` — three-way drift
  test (pytest, no Java toolchain).
- `mvn -B verify` in `test.yml` and in the release workflow — compilability
  and jar assembly.
- Existing brownfield tests (`tests/graph/test_brownfield_*.py`,
  `tests/ast/test_brownfield_events.py`, fixtures) — unchanged; they continue
  to prove the parser contract for paste-in definitions, and usage-site
  parsing is identical for JAR-provided definitions.

## Open questions

None — distribution, coordinates, versioning, and repo shape are settled
(see Decisions).
