# jqassistant Injection-Coverage Verdict (Plan 1 de-risk spike)

**Verdict: `COVERED`** — jqassistant independently resolves Spring DI well enough to serve as
the `injects` / `upstream-consumers` oracle. `injects.cypher` may be mechanical
(`oracle_source: "jqassistant:injects.cypher"`). One documented nuance below
(collection injection) shapes the rule, but is not a hard gap.

- **Tool:** jqassistant CLI `2.9.1` (`neo4jv5` distribution, bundled Neo4j 5.26.20),
  Java plugin `2.9.1`. JDK 25 present on host. Not on Homebrew; installed at
  `~/jqassistant-cli/jqassistant-commandline-neo4jv5-2.9.1/` (not committed).
- **Fixture:** `tests/bank-chat-system` (5 modules, 200 scanned `.class` entries).

## Reproduce

The bundled embedded server exposes the Neo4j Browser + Bolt but **not** the REST
transactional API, so queries are run via the native `scan` + `analyze` + XML-report
path (no `cypher-shell`, no Python `neo4j` driver — Task 7's `jqa_runner` reuses this):

```bash
JQA=~/jqassistant-cli/jqassistant-commandline-neo4jv5-2.9.1/bin/jqassistant
WD=/tmp/jqa-bc   # any isolated workdir; store lands in $WD/jqassistant/store
mkdir -p "$WD/jqassistant/rules" && cp <rules>.xml "$WD/jqassistant/rules/"
BC=tests/bank-chat-system
CLASSES="java:classpath::$BC/chat-assign/target/classes,java:classpath::$BC/chat-core/chat-app/target/classes,java:classpath::$BC/chat-core/chat-contracts/target/classes,java:classpath::$BC/chat-core/chat-domain/target/classes,java:classpath::$BC/chat-core/chat-engine/target/classes"
(cd "$WD" && "$JQA" scan -f "$CLASSES" && "$JQA" analyze)
# rows: $WD/jqassistant/report/jqassistant-report.xml
```

Custom rules must be wrapped in `<group id="default">` (plain `analyze` runs only the
`default` group; `-r`/`--concepts` are not recognized in this CLI build).

## Relationships relied upon (jqassistant Java plugin)

Labels: `:Type` (class/interface), `:Method` (constructors are `:Method{name:"<init>"}`),
`:Field`, `:Parameter`, `:Annotation`.
Edges: `(:Type)-[:DECLARES]->(:Method|:Field)`, `(:Method)-[:HAS]->(:Parameter)`,
`(:Parameter)-[:OF_TYPE]->(:Type)`, `(:Member)-[:ANNOTATED_BY]->(:Annotation)-[:OF_TYPE]->(:Type)`,
`(:Type)-[:DEPENDS_ON]->(:Type)`.

## What resolves (three findings)

1. **Constructor injection, concrete types — fully resolved.** 105 clean
   `(injector, injected)` pairs via
   `(:Type)-[:DECLARES]->(:Method{name:"<init>"})-[:HAS]->(:Parameter)-[:OF_TYPE]->(:Type)`.
2. **`@Autowired` / `@Inject` annotation — fully resolved**, on both `:Method` and
   `:Field`. The `Field-[:ANNOTATED_BY]->Annotation-[:OF_TYPE]->Type` path is proven
   present (JPA/validation annotations `@Id`, `@Column`, `@NotBlank` resolve on fields);
   bank-chat simply has no `@Autowired` *fields* (constructor injection only — the one
   `@Autowired` is on `EventProcessorRegistry`'s ctor). The mechanism is symmetric.
3. **Collection injection `List<Bean>` — resolvable via `DEPENDS_ON`.** The parameter's
   declared type erases to `java.util.List`; the bean element type is **not** in
   `Parameter.OF_TYPE`, but jqassistant parses the generic `Signature` attribute and
   emits `(:Type)-[:DEPENDS_ON]->(:Type)` for the element type. Confirmed:
   `EventProcessorRegistry` `DEPENDS_ON` includes `com.bank.chat.engine.processors.EventProcessor`.

## Worked example (anchor for Task 7)

- **Class FQN:** `com.bank.chat.assign.service.DistributionService`
  (`chat-assign/.../service/DistributionService.java`: single-arg constructor).
- **Expected injected type:** `com.bank.chat.assign.service.DistributionChunkService`.
- **Confirming query result** (`CtorInjectionConcreteTypes`):
  `injector_fqn = com.bank.chat.assign.service.DistributionService`,
  `injected_type_fqn = com.bank.chat.assign.service.DistributionChunkService`,
  `ctor_sig = void <init>(com.bank.chat.assign.service.DistributionChunkService)`.
- **`@Autowired` example:** `com.bank.chat.engine.processors.EventProcessorRegistry`
  — `memberSig = void <init>(java.util.List)`, `annotation = org.springframework.beans.factory.annotation.Autowired`;
  bean element type recovered via `DEPENDS_ON -> ...EventProcessor`.

## Implications for `injects.cypher` (Task 7)

- Emit `(injector_fqn, injected_type_fqn)` from `Parameter.OF_TYPE` for ctor params
  whose type is a project type (not `java.*/` primitives).
- **False positives to filter:** primitive/wrapper params and value constructors leak in
  (e.g. `OperatorStatus(int)`); restrict `injected` to types that are themselves beans
  (`@Service`/`@Component`/`@Repository`/`@Controller`/`@Configuration`) or that
  `IMPLEMENTS` an interface with ≥1 bean implementation.
- For collection injection, union `Parameter.OF_TYPE` (erased) with
  `injector-[:DEPENDS_ON]->bean` intersected against the bean set; document the
  heuristic in the rule header.
- The rule's mechanical output still enters the **bank-chat calibration gate**
  (Task 10/15): any residual divergence vs manual truth is caught before shopizer/petclinic.
