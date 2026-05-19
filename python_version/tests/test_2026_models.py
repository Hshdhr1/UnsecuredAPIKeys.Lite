import asyncio
from python_version.src.providers.anthropic import AnthropicProvider
from python_version.src.providers.openai import OpenAIProvider

def test_2026_models():
    anthropic = AnthropicProvider()
    assert anthropic.DEFAULT_MODEL == "claude-sonnet-4-6", f"Expected claude-sonnet-4-6, got {anthropic.DEFAULT_MODEL}"
    print("Anthropic 2026 model: OK")

if __name__ == "__main__":
    test_2026_models()
