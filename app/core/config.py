import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHROMA_API_KEY = os.getenv("CHROMA_API_KEY")
CHROMA_TENANT = os.getenv("CHROMA_TENANT")
CHROMA_DATABASE = os.getenv("CHROMA_DATABASE")
DATABASE_URL = os.getenv("DATABASE_URL")
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not found in .env file")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not found in .env file")

if not CHROMA_API_KEY or not CHROMA_TENANT or not CHROMA_DATABASE:
    raise RuntimeError("Chroma Cloud credentials missing in .env file")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found in .env file")

if not UPSTASH_REDIS_REST_URL or not UPSTASH_REDIS_REST_TOKEN:
    raise RuntimeError("Upstash Redis credentials missing in .env file")