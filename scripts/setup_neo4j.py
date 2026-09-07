"""Neo4j schema setup script.

Creates constraints, indexes, and vector index.
Run: python scripts/setup_neo4j.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.memory import Neo4jMemory


def main():
    print("Setting up Neo4j schema...")
    config = load_config()
    memory = Neo4jMemory(config)
    
    try:
        memory.connect()
        memory.setup_schema()
        print("✓ Neo4j schema setup complete")
        
        # Verify
        with memory.session() as session:
            result = session.run("SHOW CONSTRAINTS")
            constraints = list(result)
            print(f"  Constraints: {len(constraints)}")
            
            result = session.run("SHOW INDEXES")
            indexes = list(result)
            print(f"  Indexes: {len(indexes)}")
            
    except Exception as e:
        print(f"✗ Failed: {e}")
        print("  Make sure Neo4j is running and credentials are correct.")
        print(f"  URI: {config.neo4j.uri}")
        print(f"  User: {config.neo4j.username}")
    finally:
        memory.close()


if __name__ == "__main__":
    main()
