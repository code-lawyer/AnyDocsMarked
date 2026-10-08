"""rag-retriever: a local-first document retrieval engine for agents.

The agent owns the LLM. This package only does the "front half" of RAG:
extract -> chunk -> embed -> store, and similarity search. It never answers
questions itself; `search()` returns the relevant passages and the agent
reasons over them.
"""

__version__ = "2.1.0"
