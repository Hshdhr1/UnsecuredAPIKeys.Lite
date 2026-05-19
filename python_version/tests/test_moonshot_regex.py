import re
from python_version.src.providers.moonshot import MoonshotAIProvider

def test_moonshot_regex():
    provider = MoonshotAIProvider()
    pattern = list(provider.regex_patterns)[0]
    test_key = "sk-si6WPU0bbJIa5yALpRHfn96iZjWD1H0halJPmGZbS8eP4jqp"

    match = re.search(pattern, test_key)
    if match and match.group(0) == test_key:
        print(f"Regex match successful for: {test_key}")
    else:
        print(f"Regex match FAILED for: {test_key}")
        if match:
            print(f"Matched instead: {match.group(0)}")
        exit(1)

    # Test key format validator
    if provider.is_valid_key_format(test_key):
        print("is_valid_key_format: OK")
    else:
        print("is_valid_key_format: FAILED")
        exit(1)

if __name__ == "__main__":
    test_moonshot_regex()
