"""
Quick test script: query the persisted Chroma store and print top matches.

Run after ingest.py:
    python query.py "does tier 2 include 3D renderings?"
"""

import sys
import chromadb
from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from ingest import CHROMA_DIR, COLLECTION_NAME, EMBED_MODEL_NAME


import io

# Force UTF-8 stdout to avoid Windows console UnicodeEncodeErrors
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "What happens if you find problems after opening the walls?"

    embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL_NAME)
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    chroma_collection = chroma_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)

    index = VectorStoreIndex.from_vector_store(vector_store, embed_model=embed_model)
    retriever = index.as_retriever(similarity_top_k=3)

    results = retriever.retrieve(query)
    print(f"\nQuery: {query}\n")
    for i, r in enumerate(results, 1):
        print(f"--- Result {i} (score: {r.score:.3f}) ---")
        print(f"Source: {r.node.metadata.get('source_doc')} | Section: {r.node.metadata.get('section_title')}")
        print(r.node.text[:200].replace("\n", " ") + "...")
        print()


if __name__ == "__main__":
    main()
