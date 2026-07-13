"""
Ingest the company knowledge base into a local, free, persisted Chroma vector store.

Run once (or whenever the source .md files change):
    python ingest.py

Requires:
    pip install chromadb sentence-transformers llama-index llama-index-vector-stores-chroma llama-index-embeddings-huggingface
"""

import os
import chromadb
from llama_index.core import VectorStoreIndex, StorageContext, Document
from llama_index.core.schema import TextNode
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from chunker import CHUNKERS, chunk_file

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CHROMA_DIR = os.path.join(os.path.dirname(__file__), "chroma_db")
COLLECTION_NAME = "company_kb"

# Free, local, no API key needed. ~130MB model, runs on CPU fine for this corpus size.
EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def build_nodes() -> list[TextNode]:
    nodes = []
    for filename in CHUNKERS:
        path = os.path.join(DATA_DIR, filename)
        chunks = chunk_file(path, filename)
        for i, c in enumerate(chunks):
            node = TextNode(
                text=c.text,                 # what the LLM sees when this chunk is retrieved
                metadata=c.metadata,
                id_=f"{filename}::{i}",
            )
            # Store the embedding-time text (with contextual prefix) separately so
            # LlamaIndex embeds the prefixed version but returns the clean version.
            node.metadata["_embed_text"] = c.embed_text
            nodes.append(node)
    return nodes


def main():
    print(f"Loading embedding model: {EMBED_MODEL_NAME} (first run downloads ~130MB)...")
    embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL_NAME)

    nodes = build_nodes()
    print(f"Built {len(nodes)} nodes from source documents.")

    # Swap each node's text to the contextual/prefixed version just for embedding,
    # then swap back so the stored/retrieved content is the clean version.
    for node in nodes:
        clean_text = node.text
        node.text = node.metadata.pop("_embed_text")
        node.embedding = embed_model.get_text_embedding(node.text)
        node.text = clean_text

    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    chroma_collection = chroma_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    index = VectorStoreIndex(
        nodes=nodes,
        storage_context=storage_context,
        embed_model=embed_model,
    )

    print(f"\nIngested {len(nodes)} chunks into Chroma collection '{COLLECTION_NAME}'")
    print(f"Persisted at: {CHROMA_DIR}")
    print("Done. Run query.py to test retrieval.")


if __name__ == "__main__":
    main()
