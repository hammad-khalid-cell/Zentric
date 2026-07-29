"""Chroma Cloud collection used for FAQ retrieval (RAG).

The client is built **lazily**, on first use, rather than at import time. Building
it at import made merely *importing* anything in the FAQ chain
(`app.services.vector_store` <- `app.agents.faq_agent` <- `app.graph.nodes`) perform
a network round-trip, so a DNS blip failed the collection of test modules that never
touch RAG at all, and the whole suite errored out before running. Deferring it keeps
imports pure and side-effect-free; the network cost lands only on code that actually
retrieves.
"""
import chromadb

from app.core.config import CHROMA_API_KEY, CHROMA_TENANT, CHROMA_DATABASE

COLLECTION_NAME = "faqs"

_client = None


def get_client():
    """The Chroma Cloud client, built once per process on first use and cached."""
    global _client
    if _client is None:
        _client = chromadb.CloudClient(
            tenant=CHROMA_TENANT,
            database=CHROMA_DATABASE,
            api_key=CHROMA_API_KEY,
        )
    return _client


def get_collection():
    return get_client().get_or_create_collection(name=COLLECTION_NAME)
