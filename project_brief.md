KNOWLEDGE MODULE (stage 7): RAG over fake policy documents with citations
for every answer. Use LangChain only for loading and chunking documents.
Use Postgres with pgvector or Chroma for vector search. Decisions with
legal or financial effect use plain tested rules, never model output.

LANGGRAPH (stage 5 extra): rebuild the drafter agent as a LangGraph
workflow with a human approval interrupt. The gateway, permissions and
audit log stay plain Python and must not depend on LangChain or LangGraph.