import chromadb

CHROMA_PATH = "chroma_db"
COLLECTION_NAME = "faqs"

_client = chromadb.PersistentClient(path=CHROMA_PATH)


def get_collection():
    return _client.get_or_create_collection(name=COLLECTION_NAME)