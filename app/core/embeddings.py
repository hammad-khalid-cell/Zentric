from google import genai
from app.core.config import GEMINI_API_KEY

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

EMBEDDING_MODEL = "gemini-embedding-2"


def embed_text(text: str) -> list[float]:
    result = gemini_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
    )
    return result.embeddings[0].values


def embed_batch(texts: list[str]) -> list[list[float]]:
    return [embed_text(t) for t in texts]