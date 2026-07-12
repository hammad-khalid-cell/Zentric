import chromadb
from app.core.config import CHROMA_API_KEY, CHROMA_TENANT, CHROMA_DATABASE

COLLECTION_NAME = "faqs"

_client = chromadb.CloudClient(
    tenant=CHROMA_TENANT,
    database=CHROMA_DATABASE,
    api_key=CHROMA_API_KEY,
)


def get_collection():
    return _client.get_or_create_collection(name=COLLECTION_NAME)