"""In-memory knowledge index built from knowledge-layer objects (no Neo4j, no LLM).

``builder.build_document_index`` turns a NormalizedDocument + chunks + parsed tables +
profile + glossary into a DocumentIndex (entities, claims, relations, procedures, chunks,
sections). ``store.IndexStore`` implements KnowledgeService over one or more indexes with
BM25 + vector + optional reranker retrieval. Both the on-disk backend and the upload path
use the same code, so the agents behave identically for the manual and for a fresh PDF.
"""
