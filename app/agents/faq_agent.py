from app.core.embeddings import embed_text
from app.services.vector_store import get_collection
from app.core.groq_client import client


def retrieve_relevant_faqs(query: str, top_k: int = 3) -> list[dict]:
    query_vector = embed_text(query)
    collection = get_collection()

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=top_k,
    )

    matches = []
    for i in range(len(results["ids"][0])):
        matches.append({
            "id": results["ids"][0][i],
            "distance": results["distances"][0][i],
            "category": results["metadatas"][0][i]["category"],
            "question": results["metadatas"][0][i]["question"],
            "answer": results["metadatas"][0][i]["answer"],
        })

    return matches


def run_faq_agent(user_message: str) -> str:
    matches = retrieve_relevant_faqs(user_message, top_k=3)

    context_block = "\n".join(
        f"- Q: {m['question']}\n  A: {m['answer']}" for m in matches
    )

    system_prompt = (
        "You are a professional WhatsApp customer support assistant for a "
        "Pakistani courier company. Use ONLY the FAQ information below to "
        "answer the customer's question — do not invent policies or details "
        "not present in the FAQs. If none of the FAQs actually answer the "
        "question, say you're not sure and offer to connect them to a human. "
        "Reply in the same language/style the customer used (English or "
        "Roman Urdu), professionally but warmly, in 1-3 short sentences, "
        "no markdown."
    )

    user_prompt = (
        f"Relevant FAQs:\n{context_block}\n\n"
        f"Customer's question: {user_message}"
    )

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )

    return response.choices[0].message.content.strip()