# Company Knowledge Base — RAG Ingestion Pipeline

Free, local, no-API-key pipeline that chunks the three company markdown docs
and stores them in a persisted Chroma vector database for retrieval.

## Setup

```bash
pip install -r requirements.txt
python ingest.py
```

First run downloads the `BAAI/bge-small-en-v1.5` embedding model (~130MB,
runs on CPU, no API key, completely free). This only happens once — it's
cached locally afterward.

This creates `chroma_db/` in this folder — a persisted, on-disk vector store.
Delete that folder and re-run `ingest.py` any time you edit the source `.md`
files in `data/`.

## Test retrieval

```bash
python query.py "does tier 2 include 3D renderings?"
python query.py "what happens if you find problems after opening the walls?"
python query.py "how many rounds of revisions do I get?"
```

## Files

- `chunker.py` — custom markdown-aware chunker. Splits each doc by its
  natural structure (FAQ = one chunk per Q&A, Services = one chunk per tier
  + a separate comparison-table chunk, Company Info = one chunk per section).
  Every chunk gets a contextual prefix (`Document: ... / Section: ...`)
  added *only* for embedding — the LLM still sees clean chunk text at
  answer time.
- `ingest.py` — embeds all chunks and writes them into `chroma_db/`.
- `query.py` — quick CLI to sanity-check retrieval quality.

## What's next: building the actual agent

This pipeline only covers retrieval. To get to "answer questions + book a
meeting," you'll want a `FunctionAgent` (LlamaIndex) or a LangChain agent
wired to two tools:

1. **`search_knowledge_base(query: str) -> str`**
   Wraps `index.as_retriever(similarity_top_k=3)` from `query.py` and
   returns the joined chunk text for the LLM to answer from.

2. **`book_meeting(name: str, email: str, datetime: str) -> str`**
   Calls the Google Calendar API (`google-api-python-client` +
   `google-auth-oauthlib`) to create a calendar event. You'll need a
   Google Cloud project with the Calendar API enabled and either:
   - OAuth consent flow (if booking onto *the user's* calendar), or
   - A service account + your own calendar ID (if booking onto *your*
     business calendar, which is almost certainly what you want here —
     the visitor doesn't need their own Google account).

Suggested system prompt behavior for the agent: always try
`search_knowledge_base` first for factual questions about pricing,
process, timelines, etc.; only call `book_meeting` once you've collected
name, email, and a specific date/time from the user — don't call it
speculatively.

Happy to write `agent.py` and `calendar_tool.py` next once you're ready
for that piece.
