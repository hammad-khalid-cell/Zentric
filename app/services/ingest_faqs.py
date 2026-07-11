import json
from pathlib import Path
from app.core.embeddings import embed_text
from app.services.vector_store import get_collection

FAQ_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "logistics_customer_support_faqs.json"


def load_faqs() -> list[dict]:
    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["faqs"]


def ingest():
    faqs = load_faqs()
    collection = get_collection()

    ids = []
    documents = []
    embeddings = []
    metadatas = []

    for faq in faqs:
        # Embed question + answer together so retrieval matches on both
        combined_text = f"{faq['question']} {faq['answer']}"
        vector = embed_text(combined_text)

        ids.append(faq["id"])
        documents.append(combined_text)
        embeddings.append(vector)
        metadatas.append({
            "category": faq["category"],
            "question": faq["question"],
            "answer": faq["answer"],
        })

        print(f"Embedded {faq['id']}: {faq['question'][:50]}...")

    collection.upsert(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    print(f"\nIngested {len(faqs)} FAQs into Chroma collection '{collection.name}'.")


if __name__ == "__main__":
    ingest()