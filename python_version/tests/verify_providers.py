import asyncio
from python_version.src.providers.registry import ApiProviderRegistry

async def verify_providers():
    registry = ApiProviderRegistry()
    providers = registry.get_all_providers()
    print(f"Found {len(providers)} providers:")
    for p in providers:
        print(f" - {p.provider_name} ({p.api_type})")
        print(f"   Patterns: {list(p.regex_patterns)}")

    print("\nVerification successful.")

if __name__ == "__main__":
    asyncio.run(verify_providers())
