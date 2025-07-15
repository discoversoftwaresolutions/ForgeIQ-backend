import os
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-4")

async def generate_code_with_codex(prompt: str, temperature: float = 0.3, max_tokens: int = 1024) -> str:
    """Generate source code using OpenAI Codex/GPT models."""
    try:
        response = await openai.ChatCompletion.acreate(
            model=CODEX_MODEL,
            messages=[
                {"role": "system", "content": "You are a software engineer that writes production-grade code."},
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"# Error generating code: {str(e)}"
