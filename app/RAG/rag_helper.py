from dotenv import load_dotenv
import os
from openai import OpenAI

# -------------------------------------------------------------
# WINDOWS FIX: Mock 'readline' if missing (required by pinecone)
import sys
try:
    import readline
except ImportError:
    try:
        import pyreadline3
        sys.modules["readline"] = pyreadline3
    except ImportError:
        # Fallback: create a dummy module if pyreadline3 is also missing/failing
        from unittest.mock import MagicMock
        sys.modules["readline"] = MagicMock()
# -------------------------------------------------------------

from pinecone import Pinecone


load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))


def generate_response(query: str, user_id: str, chatbot_title: str):
    """Search Pinecone (user-specific index) and return AI response with context and usage."""

    # Build index name per user
    from app.RAG.pdf_processor import sanitize_id
    index_name = f"snobbots-{sanitize_id(user_id.lower().replace(' ', '_'))}"

    # ✅ Check if index exists
    if index_name not in pc.list_indexes().names():
        return f"⚠️ No knowledge base found. Please upload documents first.", {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}

    index = pc.Index(index_name)
    
    if not chatbot_title:
        raise ValueError("chatbot_title is required to create a namespace")
    namespace = chatbot_title.strip().lower().replace(" ", "_")

    # Embedding for query
    embed_resp = client.embeddings.create(
        model="text-embedding-3-large",
        input=query
    )
    query_embedding = embed_resp.data[0].embedding

    # Query Pinecone for most relevant chunks
    results = index.query(
        namespace=namespace,
        vector=query_embedding,
        top_k=3,
        include_metadata=True
    )
    top_chunks = [match["metadata"]["chunk_text"] for match in results["matches"]]
    context = "\n\n".join(top_chunks) if top_chunks else "No context found."

    # Prompt for LLM
    prompt = f"""You are a helpful chatbot assistant. Use the context to answer.

Context:
{context}

Question:
{query}

Answer:"""

    # Get LLM response (non-streaming to get usage info)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    full_text = response.choices[0].message.content
    usage = {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens
    }

    return full_text, usage