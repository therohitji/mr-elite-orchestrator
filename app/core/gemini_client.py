import os
from google import genai
from google.genai import types

from dotenv import load_dotenv

load_dotenv()

# Initialize the client (new google-genai SDK)
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

MODEL_NAME = "gemini-2.5-flash"
EMBEDDING_MODEL = "gemini-embedding-001"


def call_gemini(prompt: str) -> str:
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )
    return response.text


async def call_gemini_text(prompt: str) -> str:
    """
    Call Gemini for text responses (not JSON).
    Used for chat/query responses that need natural language output.
    """
    import asyncio
    try:
        # Run in thread to avoid blocking
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,  # Higher temperature for natural responses
            ),
        )
        return response.text
    except Exception as e:
        print(f"⚠️  Gemini text error: {e}")
        raise


async def call_gemini_analysis(prompt: str) -> str:
    """
    Call Gemini for workflow analysis with strict JSON output.
    Used for Memory Layer ingestion (workflow compression).
    """
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
        return response.text
    except Exception as e:
        print(f"⚠️  Analysis error: {e}")
        # Return empty extraction on error
        return '{"episode": null, "semantic_facts": [], "sentiment_score": 0.0, "has_errors": true}'


async def call_gemini_embedding(text: str) -> list:
    """Generate 768-dim embedding using Gemini gemini-embedding-001."""
    try:
        response = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(
                output_dimensionality=768,
            ),
        )
        return response.embeddings[0].values
    except Exception as e:
        print(f"⚠️  Embedding error: {e}")
        return [0.0] * 768  # Fallback: zero vector


async def call_gemini_embedding_batch(texts: list[str], batch_size: int = 100) -> list[list]:
    """
    Generate 768-dim embeddings for multiple texts in batched API calls.
    Gemini supports up to 100 texts per request.
    Returns list of embedding vectors in the same order as input texts.
    """
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            response = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=batch,
                config=types.EmbedContentConfig(
                    output_dimensionality=768,
                ),
            )
            all_embeddings.extend([e.values for e in response.embeddings])
        except Exception as e:
            print(f"⚠️  Batch embedding error (batch {i // batch_size}): {e}")
            # Fallback: zero vectors for this batch
            all_embeddings.extend([[0.0] * 768] * len(batch))

    return all_embeddings