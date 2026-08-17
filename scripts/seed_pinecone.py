"""Embed the festival corpus and upsert it into Pinecone.

Embeddings come from Pinecone's hosted inference API, so this costs nothing
against the $9 LLMod.ai budget.

Usage:  python scripts/seed_pinecone.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, embeddings  # noqa: E402
from app.stores import corpus  # noqa: E402

BATCH = 32


def main() -> None:
    if not config.pinecone_enabled():
        raise SystemExit("Set PINECONE_API_KEY first.")
    if not config.embeddings_enabled():
        raise SystemExit(
            "Embeddings are not configured. For EMBED_PROVIDER=llm set LLM_API_KEY and "
            "LLM_EMBED_MODEL; for EMBED_PROVIDER=pinecone set PINECONE_API_KEY."
        )

    festivals = corpus.load_festivals()
    if not festivals:
        raise SystemExit("data/festivals.json is empty.")

    from pinecone import Pinecone, ServerlessSpec

    client = Pinecone(api_key=config.PINECONE_API_KEY)

    dimension = embeddings.dimension()
    print(f"embedding provider '{config.EMBED_PROVIDER}' returns {dimension}-dimensional vectors")

    existing = {index["name"] for index in client.list_indexes()}
    if config.PINECONE_INDEX not in existing:
        print(f"creating index {config.PINECONE_INDEX} (dim={dimension}, cosine)")
        client.create_index(
            name=config.PINECONE_INDEX,
            dimension=dimension,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        while not client.describe_index(config.PINECONE_INDEX).status["ready"]:
            time.sleep(2)

    index = client.Index(config.PINECONE_INDEX)
    total = 0

    for start in range(0, len(festivals), BATCH):
        batch = festivals[start : start + BATCH]
        texts = [corpus.embedding_text(festival) for festival in batch]
        vectors = [
            {
                "id": festival["id"],
                "values": vector,
                "metadata": {
                    "name": festival.get("name") or "",
                    "country": festival.get("country") or "",
                    "tier": festival.get("tier") or "",
                    "category": festival.get("category") or "",
                    "themes": [theme for theme in (festival.get("themes") or []) if theme],
                },
            }
            for festival, vector in zip(batch, embeddings.embed(texts, input_type="passage"))
        ]
        index.upsert(vectors=vectors, namespace=config.PINECONE_NAMESPACE)
        total += len(vectors)
        print(f"upserted {total}/{len(festivals)}")

    print(f"done — namespace '{config.PINECONE_NAMESPACE}' in index '{config.PINECONE_INDEX}'")


if __name__ == "__main__":
    main()
