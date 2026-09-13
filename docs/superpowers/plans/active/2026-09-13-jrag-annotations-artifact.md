# jrag-annotations Maven Central Artifact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the brownfield annotation definitions as a versioned, signed Maven Central artifact `io.github.humanbean17:jrag-annotations` (initial `1.0.0`, independently versioned), with a canonical compilable `annotations/` module, a three-way drift test, a manual-dispatch release workflow, a CI compile guard, and updated docs.

**Architecture:** A new `annotations/` Maven module at the repo root is the single source of truth for the annotation surface (16 Java files: 6 annotations, 5 repeatable containers, 5 enums). Nothing in the Python package reads or ships it. A pytest `_meta` test binds the module to the parser vocabulary, the `CONFIGURATION.md` §4.3 fenced blocks, and the test fixtures. A `workflow_dispatch`-only GitHub Actions workflow builds, signs, and publishes to the Sonatype Central Portal on demand, tagged `annotations-vX.Y.Z` (disjoint from Python's `v*` tags).

**Tech Stack:** Java 8 bytecode (`maven.compiler.release=8`, built on JDK 17 in CI / local JDK 25), Maven 3.9 (source/javadoc/flatten/gpg plugins + `central-publishing-maven-plugin` under a `release` profile), pytest structural tests via `yaml.safe_load`.

**Spec:** `docs/superpowers/specs/active/2026-09-13-jrag-annotations-artifact-design.md`

## Global Constraints

- Work in the worktree `.agents/worktrees/worktree-2026-09-13`; all paths below are relative to its root.
- Python: use `.venv/bin/python` / `.venv/bin/python -m pytest` only. Before any pytest run: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.yml tests/*/.java-codebase-rag.hosts`.
- **No changes under `src/java_codebase_rag/`** — the parser is untouched (spec Non-goal). Tests only *read* its constants/files.
- Coordinates verbatim: `io.github.humanbean17:jrag-annotations`; initial version `1.0.0`; release tag prefix `annotations-v`; secrets `CENTRAL_PORTAL_USERNAME`, `CENTRAL_PORTAL_TOKEN`, `GPG_PRIVATE_KEY`, `GPG_PASSPHRASE` in the `release` environment.
- Artifact is compile-time only: every annotation `@Retention(RetentionPolicy.SOURCE)`; consumer scope `provided`/`compileOnly`.
- License MIT; developer Dmitry Teryaev `<doudmitry@gmail.com>` (GitHub id `humanbean17`); repo URL `https://github.com/HumanBean17/jrag`.
- The 16-type surface (member signatures) is fixed by `docs/CONFIGURATION.md` §4.3 fenced blocks — copy shapes verbatim, only adding the package declaration and javadoc.
- Never commit `annotations/target/` build output.

---

### Task 1: `annotations/` Maven module (pom + 16 sources)

**Files:**
- Create: `annotations/pom.xml`
- Create: `annotations/src/main/java/io/github/humanbean17/jrag/annotations/` — 16 files, one public top-level type each: `CodebaseRole.java`, `CodebaseCapability.java`, `CodebaseCapabilities.java`, `CodebaseHttpRoute.java`, `CodebaseHttpRoutes.java`, `CodebaseAsyncRoute.java`, `CodebaseAsyncRoutes.java`, `CodebaseHttpClient.java`, `CodebaseHttpClients.java`, `CodebaseProducer.java`, `CodebaseProducers.java`, `CodebaseRoleKind.java`, `CodebaseCapabilityKind.java`, `CodebaseHttpMethod.java`, `CodebaseClientKind.java`, `CodebaseProducerKind.java`
- Modify: `.gitignore` (add `annotations/target/`)

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces (later tasks depend on exactly this):
  - A module where `mvn -B -f annotations/pom.xml verify` yields `BUILD SUCCESS` and produces `annotations/target/jrag-annotations-1.0.0.jar`, `jrag-annotations-1.0.0-sources.jar`, `jrag-annotations-1.0.0-javadoc.jar`.
  - **POM contract:** groupId `io.github.humanbean17`; artifactId `jrag-annotations`; version `${revision}` with property `revision` defaulting to `1.0.0`; packaging `jar`; `maven.compiler.release=8`; UTF-8 source encoding; `name`, `description`, `url`, `licenses` (MIT), `developers` (one), `scm` (connection/developerConnection/url to the GitHub repo) — all required for Central validation. Plugins: `maven-source-plugin` (jar-no-fork) and `maven-javadoc-plugin` (jar; `doclint` none so stylistic javadoc rules never break builds) in the main build; a `release` **profile** containing `flatten-maven-plugin` (resolves `${revision}` into the deployed pom — without it deploy produces `${revision}`-named files), `maven-gpg-plugin` (sign at verify), and `sonatype` `central-publishing-maven-plugin` (`publishingServerId` `central`). Plain `mvn verify` must succeed with **no** signing, publishing, or credentials.
  - **Type contract** (member signatures fixed; every annotation also carries `@Target`/`@Retention(SOURCE)`, the repeatable ones `@Repeatable(<Container>.class)`; containers carry the same `@Target`/`@Retention` as their contained annotation):
    - `enum CodebaseRoleKind { CONTROLLER, SERVICE, REPOSITORY, COMPONENT, CONFIG, ENTITY, CLIENT, MAPPER, DTO }`
    - `enum CodebaseCapabilityKind { MESSAGE_LISTENER, MESSAGE_PRODUCER, HTTP_CLIENT, SCHEDULED_TASK, EXCEPTION_HANDLER }`
    - `enum CodebaseHttpMethod { GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS }`
    - `enum CodebaseClientKind { feign_method, rest_template, web_client }`
    - `enum CodebaseProducerKind { kafka_send, stream_bridge_send }`
    - `@interface CodebaseRole { CodebaseRoleKind value(); }` — `@Target(TYPE)`
    - `@interface CodebaseCapability { CodebaseCapabilityKind value(); }` — `@Target(TYPE)`
    - `@interface CodebaseCapabilities { CodebaseCapability[] value(); }` — `@Target(TYPE)`
    - `@interface CodebaseHttpRoute { String path(); CodebaseHttpMethod method(); }` — `@Target(METHOD)`
    - `@interface CodebaseHttpRoutes { CodebaseHttpRoute[] value(); }` — `@Target(METHOD)`
    - `@interface CodebaseAsyncRoute { String topic(); }` — `@Target(METHOD)`
    - `@interface CodebaseAsyncRoutes { CodebaseAsyncRoute[] value(); }` — `@Target(METHOD)`
    - `@interface CodebaseHttpClient { CodebaseClientKind clientKind(); String targetService() default ""; String path() default ""; CodebaseHttpMethod method(); }` — `@Target(METHOD)`
    - `@interface CodebaseHttpClients { CodebaseHttpClient[] value(); }` — `@Target(METHOD)`
    - `@interface CodebaseProducer { CodebaseProducerKind producerKind() default CodebaseProducerKind.kafka_send; String topic(); }` — `@Target(METHOD)`
    - `@interface CodebaseProducers { CodebaseProducer[] value(); }` — `@Target(METHOD)`
  - Javadoc: one sentence per type (what it declares, pointing at `docs/CONFIGURATION.md` §4.3 for usage); enough for a non-empty javadoc jar.

- [ ] **Step 1: Create the pom and the 16 source files**

Per the POM and type contracts above. Package is `io.github.humanbean17.jrag.annotations` in every source file. Javadoc is plain sentences — no tags beyond what's natural.

- [ ] **Step 2: Build and verify locally**

Run: `mvn -B -f annotations/pom.xml verify`
Expected: `BUILD SUCCESS`; `annotations/target/` contains `jrag-annotations-1.0.0.jar`, `-sources.jar`, `-javadoc.jar`; no signing or publishing attempted (no `release` profile active).

- [ ] **Step 3: Ignore build output**

Add `annotations/target/` to `.gitignore` (near the existing build/tooling ignores).

- [ ] **Step 4: Commit**

Run: `git add annotations/ .gitignore && git commit -m "feat(annotations): canonical Maven module for the brownfield annotation surface"`

---

### Task 2: Three-way consistency test (module ↔ parser ↔ docs ↔ fixtures)

**Files:**
- Test: `tests/_meta/test_annotations_module_consistency.py`

**Interfaces:**
- Consumes:
  - Module sources from Task 1 (`annotations/src/main/java/io/github/humanbean17/jrag/annotations/*.java`).
  - Parser constants, imported (not text-scanned): `java_codebase_rag.ast.ast_java.CODEBASE_ROUTE_ANNOTATIONS`, `CODEBASE_HTTP_CLIENT_ANNOTATIONS`, `CODEBASE_PRODUCER_ANNOTATIONS`, `_BROWNFIELD_SHADOWABLE_HTTP_FRAMEWORK_METHOD_ANNOTATIONS`; `java_codebase_rag.graph.java_ontology.VALID_ROLES`, `VALID_CAPABILITIES`, `VALID_CLIENT_KINDS`, `VALID_PRODUCER_KINDS`.
  - Role/capability annotation names have **no** frozenset constant — the parser compares inline (`ann.name == "CodebaseRole"` in `graph_enrich.py` ~845, `"CodebaseCapability"` ~861, `"CodebaseCapabilities"` ~877; `ast_java.py` ~521/556). Bind these via source-text assertions, not imports.
  - `docs/CONFIGURATION.md` §4.3 (fenced ` ```java ` blocks between the `### 4.3 Source stubs` and `### 4.4` headings); `tests/fixtures/brownfield_route_stubs/` and `tests/fixtures/brownfield_client_stubs/` (`**/*.java`).
- Produces: a pytest module other tasks extend (Task 5 adds a docs-mention test here); helpers for extracting declarations from Java source text: a function returning declared annotation names (`public @interface X`), one returning enum-name → constants map (`public enum X` with upper-case constant lines), and a normalizer producing comparable declaration forms.

**Test design — three tests, exact expectations:**

- [ ] **Step 1: Write `test_module_matches_parser_vocabulary`**

Extraction from module sources (declaration recognition only — usage examples elsewhere are ignored). Asserts:
1. Module annotation-name set equals `CODEBASE_ROUTE_ANNOTATIONS | CODEBASE_HTTP_CLIENT_ANNOTATIONS | CODEBASE_PRODUCER_ANNOTATIONS | {"CodebaseRole", "CodebaseCapability", "CodebaseCapabilities"}` — exact set equality, both directions.
2. Enum constants, exact set equality per pair: `CodebaseRoleKind` ↔ `VALID_ROLES`; `CodebaseCapabilityKind` ↔ `VALID_CAPABILITIES`; `CodebaseClientKind` ↔ `VALID_CLIENT_KINDS`; `CodebaseProducerKind` ↔ `VALID_PRODUCER_KINDS`.
3. `CodebaseHttpMethod` constants ⊆ `_BROWNFIELD_SHADOWABLE_HTTP_FRAMEWORK_METHOD_ANNOTATIONS` (subset, not equality — that set also contains framework mapping-annotation names).
4. `graph_enrich.py` and `ast_java.py` source text both contain the literals `CodebaseRole`, `CodebaseCapability`, `CodebaseCapabilities`.

- [ ] **Step 2: Write `test_docs_section_43_blocks_match_module`**

Collect `public @interface` / `public enum` declarations from §4.3 fenced java blocks (16 expected types — anything else in the blocks, e.g. usage-example classes, is not a declaration and is skipped). Normalization on both docs and module text before comparing per type name: drop `package`/`import` lines, all comments, meta-annotation usage lines (`@Target`, `@Retention`, `@Repeatable`), and blank lines; collapse runs of whitespace. Assert per type: same declaration kind (annotation vs enum), and for annotations an identical member list (member name, type, and default-expression text); for enums an identical constant list. Failure messages must name the drifted type.

- [ ] **Step 3: Write `test_fixture_stubs_match_module`**

Walk both fixture trees; for every declared type: the module declares a type with the same name and kind; for every fixture enum, its constant set equals the module enum's. (Fixtures keep their `com.example.rag` package — the point is name/shape parity, not package parity.)

- [ ] **Step 4: Run the tests**

Run: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.yml tests/*/.java-codebase-rag.hosts && .venv/bin/python -m pytest tests/_meta/test_annotations_module_consistency.py -v`
Expected: 3 PASS. A failure means real drift — reconcile by fixing the *newer* artifact (module wording) or flagging a genuine parser/docs mismatch to the maintainer; do not weaken assertions to pass.

- [ ] **Step 5: Commit**

Run: `git add tests/_meta/test_annotations_module_consistency.py && git commit -m "test(_meta): bind annotations module to parser vocabulary, docs, fixtures"`

---

### Task 3: `release-annotations.yml` workflow + structural tests

**Files:**
- Create: `.github/workflows/release-annotations.yml`
- Test: `tests/package/test_release_annotations_workflow.py`

**Interfaces:**
- Consumes: Task 1's module and its `release` profile (`-P release -Drevision=<v>` activates flatten+gpg+central publishing); structural-test conventions from `tests/package/test_release_workflow.py` (local `_load`/`_triggers` helpers — that module's helpers are not importable; replicate them; PyYAML parses bare `on:` as boolean `True`, so read both spellings).
- Produces: the manual-only Maven Central release channel; the structural-test file Task 4 extends.

**Workflow contract:**
- `name: release-annotations`; triggers: **`workflow_dispatch` only**, with required string input `version` (description names the format `X.Y.Z`). No `push`/`pull_request`/tag triggers.
- Top comment header = the maintainer runbook: one-time setup (verify namespace `io.github.humanbean17` on the Sonatype Central Portal; generate a GPG signing key and publish its fingerprint to a keyserver; add the four secrets to the `release` environment), the version-immutability rule, interrupted-publish recovery (an upload stranded before Portal release resumes from the Portal's held state — never re-upload over a published version), and that releases fire only when the annotation surface changes (consistency test failing = vocabulary changed = minor bump due).
- `permissions: contents: write`; `concurrency: release-annotations` (no cancel-in-progress).
- One job `publish`, `runs-on: ubuntu-latest`, `environment: release`, steps in order:
  1. Checkout (`actions/checkout@v4`).
  2. **Version-input guard** — fail with a clear message unless the input matches `^[0-9]+\.[0-9]+\.[0-9]+$`.
  3. **Central idempotency guard** — HTTP GET to the Maven Central search API (`https://search.maven.org/solrsearch/select?q=g:"io.github.humanbean17"+AND+a:"jrag-annotations"+AND+v:"<version>"`); a response with `numFound > 0` fails the run before anything is built. (Best-effort guard — the Portal itself enforces version immutability as the hard stop.)
  4. `actions/setup-java@v4` — Temurin 17, Maven cache.
  5. Write `~/.m2/settings.xml` with server id `central` from `CENTRAL_PORTAL_USERNAME` / `CENTRAL_PORTAL_TOKEN`.
  6. Import GPG key from `GPG_PRIVATE_KEY` with passphrase `GPG_PASSPHRASE`.
  7. Build + publish: `mvn -B -f annotations/pom.xml -P release -Drevision=${{ inputs.version }} clean verify deploy`.
  8. Record: push annotated tag `annotations-v<version>` and open a GitHub Release scoped to the module (softprops/action-gh-release, `tag_name: annotations-v<version>`, generated notes).

**Test design — each verifies one invariant, expected result stated:**

- [ ] **Step 1: Write the failing structural tests**

`test_workflow_yaml_parses` — file exists, `yaml.safe_load` returns a map. `test_manual_dispatch_only_with_version_input` — triggers contain `workflow_dispatch`, no `push`/`pull_request` keys; input `version` is required. `test_release_environment_and_permissions` — job uses `environment: release`; top-level `permissions` includes `contents: write`. `test_guard_precedes_build` — the step referencing `search.maven.org` is ordered before the `setup-java` step. `test_build_uses_release_profile_and_revision` — the mvn step's run string contains `-P release` and `-Drevision=`. `test_release_tag_prefix` — the release step(s) reference `annotations-v`. `test_secrets_present` — file text contains all four secret names.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/package/test_release_annotations_workflow.py -v`
Expected: FAIL (workflow file does not exist).

- [ ] **Step 3: Write the workflow per the contract above**

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/package/test_release_annotations_workflow.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

Run: `git add .github/workflows/release-annotations.yml tests/package/test_release_annotations_workflow.py && git commit -m "ci: manual-dispatch Maven Central release workflow for jrag-annotations"`

---

### Task 4: `annotations-build` CI job in `test.yml`

**Files:**
- Modify: `.github/workflows/test.yml` (new top-level job alongside `test`)
- Test: `tests/package/test_release_annotations_workflow.py` (extend)

**Interfaces:**
- Consumes: Task 1's module (plain `mvn verify`, no profile, no credentials).
- Produces: per-PR compile guard for `annotations/**`.

**Job contract:** job id `annotations-build`; `runs-on: ubuntu-latest`; **no** `continue-on-error` (it must gate); steps: checkout → `actions/setup-java@v4` (Temurin 17, Maven cache) → `mvn -B -f annotations/pom.xml verify`. Runs on the existing `pull_request` + `push` triggers of the file (no new triggers).

- [ ] **Step 1: Write the failing structural test**

`test_test_yml_has_annotations_build_job` in the Task 3 test file: parse `.github/workflows/test.yml`; assert `jobs` contains `annotations-build`, `runs-on` is `ubuntu-latest`, the job has no truthy `continue-on-error`, one step uses `setup-java`, and one step's run string contains `mvn -B -f annotations/pom.xml verify`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/package/test_release_annotations_workflow.py::test_test_yml_has_annotations_build_job -v`
Expected: FAIL (no such job).

- [ ] **Step 3: Add the job per contract**

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/package/test_release_annotations_workflow.py -v`
Expected: 8 PASS (7 previous + 1 new).

- [ ] **Step 5: Commit**

Run: `git add .github/workflows/test.yml tests/package/test_release_annotations_workflow.py && git commit -m "ci: annotations-build compile guard in test.yml"`

---

### Task 5: Docs — dependency-first §4.3, requirements, contributor notes

**Files:**
- Modify: `docs/CONFIGURATION.md` (§4.3 only)
- Modify: `docs/CODEBASE_REQUIREMENTS.md` (§A.2.1, ~lines 214–253)
- Modify: `AGENTS.md` (Publishing section)
- Test: `tests/_meta/test_annotations_module_consistency.py` (extend)

**Interfaces:**
- Consumes: Task 1's coordinates/version; Task 2's docs-block consistency binding (the §4.3 fenced java blocks must survive the restructure byte-compatible after normalization — new prose and dependency snippets go *around* them, not inside the fences).
- Produces: the operator-facing distribution story; a docs-mention tripwire test.

**Content contract:**
- `CONFIGURATION.md` §4.3 — new subsection right after the intro paragraph, before the fenced blocks: "**Dependency (preferred)**" — Maven snippet (`io.github.humanbean17:jrag-annotations`, version `1.0.0`, `scope provided`) and Gradle snippet (`compileOnly`); one compatibility line — *annotations `1.x` vocabulary equals the jrag parser vocabulary as of jrag `0.12`; vocabulary growth ships as an annotations minor release*; one line noting Kotlin sources use the same artifact. The existing intro sentence is reframed: copy-paste becomes the **zero-dependency alternative**, fenced blocks unchanged.
- `CODEBASE_REQUIREMENTS.md` §A.2.1 — brownfield guidance prefers the Maven artifact; copy-paste from §4.3 stays as the fallback when adding a dependency isn't possible.
- `AGENTS.md` Publishing — a short paragraph: `io.github.humanbean17:jrag-annotations` is a third, **independently versioned** channel; released via manual-dispatch `release-annotations.yml`; tags `annotations-v*`; **not** part of the dual-PyPI same-version sync rule; version bumps only when the annotation surface changes (the `_meta` consistency test forces reconciliation).

- [ ] **Step 1: Write the failing docs tripwire test**

`test_docs_mention_annotations_artifact` in the Task 2 test file: asserts `CONFIGURATION.md` §4.3 contains the strings `io.github.humanbean17:jrag-annotations`, `provided`, `compileOnly`, `1.0.0`; `CODEBASE_REQUIREMENTS.md` contains the coordinates; `AGENTS.md` contains `annotations-v` and the phrase "independently".

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/_meta/test_annotations_module_consistency.py::test_docs_mention_annotations_artifact -v`
Expected: FAIL.

- [ ] **Step 3: Edit the three docs per the content contract**

Fenced java blocks in §4.3 stay untouched.

- [ ] **Step 4: Run the full consistency file**

Run: `.venv/bin/python -m pytest tests/_meta/test_annotations_module_consistency.py -v`
Expected: 4 PASS (3 from Task 2 + the tripwire) — proving the §4.3 restructure didn't drift the blocks.

- [ ] **Step 5: Commit**

Run: `git add docs/CONFIGURATION.md docs/CODEBASE_REQUIREMENTS.md AGENTS.md tests/_meta/test_annotations_module_consistency.py && git commit -m "docs: jrag-annotations Maven dependency as the primary brownfield path"`

---

### Task 6: Full-suite verification

**Files:** none created; read-only verification.

**Interfaces:**
- Consumes: everything above.
- Produces: verified-complete state, ready for `finishing-a-development-branch`.

- [ ] **Step 1: Clean stale test indexes**

Run: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.yml tests/*/.java-codebase-rag.hosts`

- [ ] **Step 2: Run the full test suite once (AGENTS.md end-of-task rule)**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (2184 pre-existing + ~12 new); heavy cocoindex/Lance e2e tests skip without `JAVA_CODEBASE_RAG_RUN_HEAVY`. If any pre-existing failure appears, compare against master to prove it predates this branch before reporting.

- [ ] **Step 3: Final build check**

Run: `mvn -B -f annotations/pom.xml verify`
Expected: `BUILD SUCCESS` (repeatable clean state).

- [ ] **Step 4: Report**

Confirm `git status` clean, all commits present, and summarize: what shipped, what remains manual (one-time Central Portal namespace verification, GPG key, secrets — then first `1.0.0` dispatch).
