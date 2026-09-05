# Enterprise Memory Graph + RAG

Long-term enterprise memory for the AI Sales Operating System.

## Architecture

```
Question
  → Embedding (deterministic hash vectors)
  → Vector Search (lexical overlap ≥ 2 tokens)
  → Knowledge Graph Search (1-hop expansion)
  → Evidence Ranking
  → Answer + Citation
```

If no evidence survives ranking, the system returns exactly:

`Không có dữ liệu trong hệ thống.`

AI answers are composed only from retrieved Memory Graph snippets. No free-form invention.

## Packages

| Path | Role |
|------|------|
| `backend/memory_graph/` | Typed nodes/edges, persistence, sync, explorer |
| `backend/rag/` | Embeddings, vector index, hybrid retriever, evidence RAG |
| `vector_store/` | `memory_graph.json`, history, RAG index |
| `knowledge/` | Synced SOP / pricing / policy / FAQ / golden calls / rulebook JSONL |
| `tests/memory_graph/` | 3200+ retrieval/citation/version/duplicate/permission tests |

## Node types

Product, SOP, Rulebook, Intent, Objection, Pricing, Policy, Promotion, Golden Call, Customer Type, Root Cause, Coaching, Compliance, FAQ, Call, Revenue Leak.

Each node carries: `id`, `version`, `source`, `timestamp`, relationships, `confidence`.

## Core relationships

- Product ↔ SOP
- Product ↔ Objection
- Objection ↔ Rule
- Customer ↔ Intent
- Golden Call ↔ Coaching
- Root Cause ↔ Revenue Leak

## Sync corpora

`POST /v1/memory-graph/sync` with optional `kind`:

- `sop`, `pricing`, `policy`, `faq`, `golden_calls`, `rulebook`

## API

- `POST /v1/memory-graph/build` — build from call analysis
- `POST /v1/memory-graph/search` — snapshot/live search
- `POST /v1/memory-graph/knowledge/search` — Search Knowledge
- `POST /v1/memory-graph/similar/objection` — Similar Objection
- `POST /v1/memory-graph/similar/call` — Similar Call
- `POST /v1/memory-graph/knowledge/update` — Update Knowledge
- `POST /v1/memory-graph/versions` — Version History
- `POST /v1/memory-graph/explore` — Graph Explorer
- `POST /v1/memory-graph/ask` — Evidence RAG
- `GET /v1/memory-graph/quality` — integrity report

## Dashboard

`enterprise-web` → **Memory Graph**

- Graph visualization + type counts
- Related knowledge search
- RAG answer with source citations
- Version history viewer

## Quality gates

- No dangling graph links
- No duplicate node content for the same type/label
- Citations resolve to existing nodes
- Retrieval returns evidence or exact no-data string
