from python_version.src.providers.openai import OpenAIProvider

def test_fake_detection():
    provider = OpenAIProvider()
    fake_keys = [
        "sk-idinahui-ebani-huesos-1234567890",
        "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "sk-fake-key-12345678901234567890",
        "sk-example-12345678901234567890"
    ]
    valid_looking_key = "sk-si6WPU0bbJIa5yALpRHfn96iZjWD1H0halJPmGZbS8eP4jqp"

    for key in fake_keys:
        if provider.is_valid_key_format(key):
            print(f"FAIL: Fake key accepted: {key}")
            exit(1)
        else:
            print(f"OK: Fake key rejected: {key}")

    if provider.is_valid_key_format(valid_looking_key):
        print(f"OK: Valid-looking key accepted")
    else:
        print(f"FAIL: Valid-looking key rejected")
        exit(1)

if __name__ == "__main__":
    test_fake_detection()
