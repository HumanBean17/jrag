# Edge Navigation Schema

> **Generated from `java_ontology.EDGE_SCHEMA` — do not edit by hand.**
> Regenerate: `.venv/bin/python scripts/generate_edge_navigation.py`

## Summary

| Edge | From | To | Cardinality | Brownfield-resolver-sourced | Member-only |
| --- | --- | --- | --- | --- | --- |
| EXTENDS | Symbol | Symbol | many_to_one | no | no |
| IMPLEMENTS | Symbol | Symbol | many_to_many | no | no |
| INJECTS | Symbol | Symbol | many_to_many | no | no |
| DECLARES | Symbol | Symbol | one_to_many | no | no |
| OVERRIDES | Symbol | Symbol | many_to_one | no | yes |
| CALLS | Symbol | Symbol | many_to_many | yes | yes |
| EXPOSES | Symbol | Route | one_to_one | yes | yes |
| DECLARES_CLIENT | Symbol | Client | one_to_many | yes | yes |
| DECLARES_PRODUCER | Symbol | Producer | one_to_many | yes | yes |
| HTTP_CALLS | Client | Route | many_to_many | yes | no |
| ASYNC_CALLS | Producer | Route | many_to_many | yes | no |

## EXTENDS

**Endpoints**: `Symbol → Symbol`
**Cardinality**: `many_to_one`
**Brownfield-resolver-sourced**: no
**Member-only** (hints): no

**Purpose**: class or interface direct supertype relation

**Attributes**:

- `dst_name` (`STRING`) — raw supertype name as written in source
- `dst_fqn` (`STRING`) — best-effort resolved FQN of the supertype
- `resolved` (`BOOLEAN`) — True iff dst_fqn was resolved to an in-graph Symbol

**Typical traversals**:

- `type_subject`: neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['EXTENDS'])
- `member_subject`: neighbors(['{id}'],'out',['EXTENDS'])
- `alien_subject`: EXTENDS connects Symbol → Symbol; use a type or member Symbol id

## IMPLEMENTS

**Endpoints**: `Symbol → Symbol`
**Cardinality**: `many_to_many`
**Brownfield-resolver-sourced**: no
**Member-only** (hints): no

**Purpose**: class implements interface relation

**Attributes**:

- `dst_name` (`STRING`) — raw interface name as written in source
- `dst_fqn` (`STRING`) — best-effort resolved FQN of the interface
- `resolved` (`BOOLEAN`) — True iff dst_fqn was resolved to an in-graph Symbol

**Typical traversals**:

- `type_subject`: neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['IMPLEMENTS'])
- `member_subject`: neighbors(['{id}'],'out',['IMPLEMENTS'])
- `alien_subject`: IMPLEMENTS connects Symbol → Symbol; use a type or member Symbol id

## INJECTS

**Endpoints**: `Symbol → Symbol`
**Cardinality**: `many_to_many`
**Brownfield-resolver-sourced**: no
**Member-only** (hints): no

**Purpose**: dependency injection edge from declaring type to injected type

**Attributes**:

- `dst_name` (`STRING`) — raw injected type name as written in source
- `dst_fqn` (`STRING`) — best-effort resolved FQN of the injected type
- `resolved` (`BOOLEAN`) — True iff dst_fqn was resolved to an in-graph Symbol
- `mechanism` (`STRING`) — injection mechanism literal (constructor, field, setter, …)
- `annotation` (`STRING`) — injection annotation simple name when present
- `field_or_param` (`STRING`) — field or parameter name for the injection site

**Typical traversals**:

- `type_subject`: neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['INJECTS'])
- `member_subject`: neighbors(['{id}'],'in',['INJECTS'])
- `alien_subject`: INJECTS connects Symbol → Symbol; use a type Symbol id

## DECLARES

**Endpoints**: `Symbol → Symbol`
**Cardinality**: `one_to_many`
**Brownfield-resolver-sourced**: no
**Member-only** (hints): no

**Purpose**: type declares member Symbol (method, constructor, nested type)

**Attributes**: _(none)_

**Typical traversals**:

- `type_subject`: neighbors(['{id}'],'out',['DECLARES'])
- `member_subject`: neighbors(['{id}'],'in',['DECLARES'])
- `alien_subject`: DECLARES connects Symbol → Symbol; use a type Symbol id for outbound members

## OVERRIDES

**Endpoints**: `Symbol → Symbol`
**Cardinality**: `many_to_one`
**Brownfield-resolver-sourced**: no
**Member-only** (hints): yes

**Purpose**: subtype method overrides supertype declared method with matching signature

**Attributes**: _(none)_

**Typical traversals**:

- `type_subject`: neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['OVERRIDES'])
- `member_subject`: neighbors(['{id}'],'out',['OVERRIDES'])
- `alien_subject`: OVERRIDES connects method Symbol → method Symbol

## CALLS

**Endpoints**: `Symbol → Symbol`
**Cardinality**: `many_to_many`
**Brownfield-resolver-sourced**: yes
**Member-only** (hints): yes

**Purpose**: intra-codebase method call from caller method to callee method

**Attributes**:

- `call_site_line` (`INT64`) — source line of the call site
- `call_site_byte` (`INT64`) — source byte offset of the call site
- `arg_count` (`INT64`) — argument count at the call site (-1 for method references)
- `confidence` (`DOUBLE`) — resolver confidence in [0.0, 1.0]
- `strategy` (`STRING`) — call-graph resolution strategy literal
- `source` (`STRING`) — call-graph source tag
- `resolved` (`BOOLEAN`) — True iff callee Symbol was resolved in-graph

**Typical traversals**:

- `type_subject`: neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['CALLS'])
- `member_subject`: neighbors(['{id}'],'out',['CALLS'])
- `alien_subject`: CALLS connects method Symbol → method Symbol

## EXPOSES

**Endpoints**: `Symbol → Route`
**Cardinality**: `one_to_one`
**Brownfield-resolver-sourced**: yes
**Member-only** (hints): yes

**Purpose**: declaring method exposes an inbound HTTP or messaging Route

**Attributes**:

- `confidence` (`DOUBLE`) — route extraction confidence in [0.0, 1.0]
- `strategy` (`STRING`) — route resolution strategy literal

**Typical traversals**:

- `type_subject`: neighbors(['{id}'],'out',['DECLARES.EXPOSES']) — or neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['EXPOSES']) — or neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['EXPOSES'])
- `member_subject`: neighbors(['{id}'],'out',['EXPOSES'])
- `alien_subject`: EXPOSES connects method Symbol → Route; use a method Symbol id

## DECLARES_CLIENT

**Endpoints**: `Symbol → Client`
**Cardinality**: `one_to_many`
**Brownfield-resolver-sourced**: yes
**Member-only** (hints): yes

**Purpose**: method declares an outbound HTTP client call site

**Attributes**:

- `confidence` (`DOUBLE`) — client declaration confidence in [0.0, 1.0]
- `strategy` (`STRING`) — client resolution strategy literal

**Typical traversals**:

- `type_subject`: neighbors(['{id}'],'out',['DECLARES.DECLARES_CLIENT']) — or neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['DECLARES_CLIENT']) — or neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['DECLARES_CLIENT'])
- `member_subject`: neighbors(['{id}'],'out',['DECLARES_CLIENT'])
- `alien_subject`: DECLARES_CLIENT connects method Symbol → Client

## DECLARES_PRODUCER

**Endpoints**: `Symbol → Producer`
**Cardinality**: `one_to_many`
**Brownfield-resolver-sourced**: yes
**Member-only** (hints): yes

**Purpose**: method declares an outbound async producer call site

**Attributes**:

- `confidence` (`DOUBLE`) — producer declaration confidence in [0.0, 1.0]
- `strategy` (`STRING`) — producer resolution strategy literal

**Typical traversals**:

- `type_subject`: neighbors(['{id}'],'out',['DECLARES.DECLARES_PRODUCER']) — or neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['DECLARES_PRODUCER']) — or neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'{direction}',['DECLARES_PRODUCER'])
- `member_subject`: neighbors(['{id}'],'out',['DECLARES_PRODUCER'])
- `alien_subject`: DECLARES_PRODUCER connects method Symbol → Producer

## HTTP_CALLS

**Endpoints**: `Client → Route`
**Cardinality**: `many_to_many`
**Brownfield-resolver-sourced**: yes
**Member-only** (hints): no

**Purpose**: resolved HTTP call from a declared Client to a target route

**Attributes**:

- `confidence` (`DOUBLE`) — pass6 match confidence in [0.0, 1.0]
- `strategy` (`STRING`) — HTTP call resolution strategy literal
- `method_call` (`STRING`) — HTTP method of the call site
- `raw_uri` (`STRING`) — uninterpolated URI template from the call site
- `match` (`STRING`) — cross_service|intra_service|ambiguous|phantom|unresolved

**Typical traversals**:

- `type_subject`: neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'out',['DECLARES_CLIENT']) then neighbors(client_ids,'out',['HTTP_CALLS'])
- `member_subject`: neighbors(['{id}'],'out',['DECLARES_CLIENT']) then neighbors(client_ids,'out',['HTTP_CALLS'])
- `route_subject`: neighbors(['{id}'],'in',['HTTP_CALLS']) then neighbors(client_ids,'in',['DECLARES_CLIENT']) for declaring method
- `alien_subject`: HTTP_CALLS connects Client→Route; use DECLARES_CLIENT from a method Symbol, or neighbors(client_id,'out',['HTTP_CALLS']) from a Client id

## ASYNC_CALLS

**Endpoints**: `Producer → Route`
**Cardinality**: `many_to_many`
**Brownfield-resolver-sourced**: yes
**Member-only** (hints): no

**Purpose**: resolved async call from a declared Producer to a topic route

**Attributes**:

- `confidence` (`DOUBLE`) — pass6 match confidence in [0.0, 1.0]
- `strategy` (`STRING`) — async call resolution strategy literal
- `direction` (`STRING`) — produce|consume async direction literal
- `raw_topic` (`STRING`) — uninterpolated topic template from the call site
- `match` (`STRING`) — cross_service|intra_service|ambiguous|phantom|unresolved

**Typical traversals**:

- `type_subject`: neighbors(['{id}'],'out',['DECLARES']) then neighbors(member_ids,'out',['DECLARES_PRODUCER']) then neighbors(producer_ids,'out',['ASYNC_CALLS'])
- `member_subject`: neighbors(['{id}'],'out',['DECLARES_PRODUCER']) then neighbors(producer_ids,'out',['ASYNC_CALLS'])
- `route_subject`: neighbors(['{id}'],'in',['ASYNC_CALLS']) then neighbors(producer_ids,'in',['DECLARES_PRODUCER']) for declaring method
- `alien_subject`: ASYNC_CALLS connects Producer→Route; use DECLARES_PRODUCER from a method Symbol, or neighbors(producer_id,'out',['ASYNC_CALLS']) from a Producer id
