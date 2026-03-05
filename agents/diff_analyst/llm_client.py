# agents/diff_analyst/llm_client.py

import os
from typing import Optional

from dotenv import load_dotenv

# Load .env for local dev. In CI/containers, env vars usually come from the environment.
load_dotenv()

_client = None  # cached AzureOpenAI client


def _get_client():
    """
    Lazy-init AzureOpenAI client to avoid import-time crashes when env vars are missing.
    """
    global _client
    if _client is not None:
        return _client

    # Import here so module import doesn't fail if openai isn't available in some test contexts.
    from openai import AzureOpenAI

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

    if not endpoint or not api_key:
        raise RuntimeError(
            "Azure OpenAI env vars missing. Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY."
        )

    _client = AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
    )
    return _client


def call_llm(system_prompt: str, user_input: str) -> str:
    """
    Calls Azure OpenAI chat completions and returns the assistant text.
    """
    client = _get_client()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    if not deployment:
        raise RuntimeError("Azure OpenAI deployment missing. Set AZURE_OPENAI_DEPLOYMENT.")

    resp = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        temperature=0,
    )
    return resp.choices[0].message.content or ""