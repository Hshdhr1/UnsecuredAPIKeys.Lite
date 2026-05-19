import sys
from sqlalchemy import create_engine
from python_version.src.database.models import Base

def test_models():
    try:
        # Use in-memory SQLite for testing syntax and initialization
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        print("Models successfully initialized and verified.")
    except Exception as e:
        print(f"Error during model verification: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_models()
