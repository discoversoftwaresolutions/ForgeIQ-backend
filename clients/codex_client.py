# forgeiq/codex_client.py

import os
import openai
import logging

logger = logging.getLogger(__name__)

openai.api_key = os.getenv("OPENAI_API_KEY")
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-4")

async def generate_code_with_codex(prompt: str) -> str:
    try:
        response = await openai.ChatCompletion.acreate(
            model=CODEX_MODEL,
            messages=[
                {"role": "system", "content": "You are an expert software engineer."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Codex generation error: {e}")
        return f"# ERROR: Code generation failed due to: {e}"
