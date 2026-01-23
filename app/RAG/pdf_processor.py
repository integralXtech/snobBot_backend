import fitz  # PyMuPDF
import docx  # python-docx
from langchain.text_splitter import RecursiveCharacterTextSplitter
from dotenv import load_dotenv
import os
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec
import json
import uuid
import re
import unicodedata
from typing import Union

# Load keys
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)
pc = Pinecone(api_key=PINECONE_API_KEY)

# OpenAI pricing (as of Sept 2025)
EMBEDDING_COST_PER_1K = 0.00013  # text-embedding-3-large


def sanitize_id(value: str) -> str:
    """
    Make a string ASCII-safe for Pinecone vector IDs.
    Removes or replaces all non-ASCII characters.
    """
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9._-]", "_", value)
    return value


def process_and_index_data(
    user_id: str,
    filename: str = None,
    file_bytes: bytes = None,
    raw_text: str = None,
    qa_json: Union[str, list] = None,
    source_type: str = None,  # NEW param to override source (e.g., "web_crawling")
    chatbot_title: str = None
):
    """
    Process data (PDF, DOCX, TXT, raw text, or QA JSON), chunk, embed, and upsert into Pinecone.

    Args:
        user_id: ID of the user
        filename: Optional filename for file (PDF, DOCX, TXT)
        file_bytes: File bytes
        raw_text: Raw text input
        qa_json: JSON string OR list with [{"question": "...", "answer": "..."}]
        source_type: Optional override for source ("web_crawling", "manual_input", etc.)
    """

    # Unique index name per user
    INDEX_NAME = f"snobbots-{sanitize_id(user_id.lower().replace(' ', '_'))}"

    # Ensure index exists
    if INDEX_NAME not in pc.list_indexes().names():
        pc.create_index(
            name=INDEX_NAME,
            dimension=3072,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1")
        )
    index = pc.Index(INDEX_NAME)

    if not chatbot_title:
        raise ValueError("chatbot_title is required to create a namespace")
    namespace = sanitize_id(chatbot_title.strip().lower().replace(" ", "_"))

    # Collect chunks with source info
    chunks = []

    # Case 1: File upload
    if file_bytes and filename:
        ext = filename.lower().split(".")[-1]

        if ext == "pdf":
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            text = "".join(page.get_text() for page in doc)

        elif ext == "docx":
            doc = docx.Document(file_bytes)
            text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

        elif ext == "txt":
            text = file_bytes.decode("utf-8")

        else:
            raise ValueError(f"Unsupported file type: {ext}")

        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        file_chunks = text_splitter.split_text(text)
        chunks.extend({"text": c, "source": filename} for c in file_chunks)

    # Case 2: Raw text (manual input or web crawling)
    if raw_text:
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        text_chunks = text_splitter.split_text(raw_text)
        chunks.extend({
            "text": c,
            "source": source_type if source_type else "raw_text"
        } for c in text_chunks)

    # Case 3: QA JSON
    if qa_json:
        # Normalize qa_json -> list
        if isinstance(qa_json, str):
            try:
                qa_pairs = json.loads(qa_json)
            except Exception as e:
                raise ValueError(f"Invalid QA JSON string format: {e}")
        elif isinstance(qa_json, list):
            qa_pairs = qa_json
        else:
            raise ValueError("qa_json must be a JSON string or a list")

        # Validate and build chunks
        if not all(isinstance(item, dict) and "question" in item and "answer" in item for item in qa_pairs):
            raise ValueError("qa_json must be a list of {question, answer} objects")

        for qa in qa_pairs:
            q = qa.get("question", "").strip()
            a = qa.get("answer", "").strip()
            if q and a:
                chunks.append({"text": f"Q: {q}\nA: {a}", "source": "qa_json"})

    # Safety check
    if not chunks:
        raise ValueError("No valid input provided (PDF/DOCX/TXT, raw_text, or qa_json required).")

    # Embed + upsert (append-only IDs)
    vectors = []
    total_tokens = 0

    for i, chunk in enumerate(chunks):
        resp = client.embeddings.create(
            model="text-embedding-3-large",
            input=chunk["text"]
        )
        embedding = resp.data[0].embedding

        # ✅ Track tokens for this request
        if hasattr(resp, "usage"):
            total_tokens += resp.usage.total_tokens

        safe_source = sanitize_id(chunk["source"])
        unique_id = sanitize_id(f"{user_id}_{safe_source}_{i}_{uuid.uuid4().hex[:8]}")

        vectors.append({
            "id": unique_id,
            "values": embedding,
            "metadata": {
                "chunk_text": chunk["text"],
                "source": chunk["source"],
                "user_id": user_id
            }
        })

    # ✅ Now safe for Pinecone
    index.upsert(vectors=vectors, namespace=namespace)

    return {
        "chunks_indexed": len(chunks),
        "index_name": INDEX_NAME,
        "namespace": namespace,
        "tokens_used": total_tokens,
    }