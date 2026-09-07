"""Environment validation script.

Checks all dependencies and services required for the pipeline.
Run: python scripts/validate_environment.py
"""

import sys
import importlib
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def check(name: str, fn) -> bool:
    """Run a check and print result."""
    try:
        result = fn()
        print(f"  ✓ {name}: {result}")
        return True
    except Exception as e:
        print(f"  ✗ {name}: {e}")
        return False


def check_python():
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    if v.minor < 10:
        raise RuntimeError(f"Python >= 3.10 required, got {version}")
    return version


def check_pydantic():
    import pydantic
    return pydantic.__version__


def check_docling():
    import docling
    return getattr(docling, "__version__", "installed")


def check_neo4j():
    import neo4j
    return neo4j.__version__


def check_httpx():
    import httpx
    return httpx.__version__


def check_rich():
    from importlib.metadata import version
    return version("rich")


def check_sentence_transformers():
    import sentence_transformers
    return sentence_transformers.__version__


def check_xxhash():
    import xxhash
    return xxhash.VERSION


def check_schemas():
    from schemas.parsed_document import ParsedDocument
    from schemas.normalized_document import NormalizedDocument
    from schemas.knowledge import DocumentKnowledge
    from schemas.claims import EngineeringClaim
    from schemas.table_schema import ClassifiedTable
    from schemas.document_profile import DocumentProfile
    from schemas.glossary import DocumentGlossary
    from schemas.validation import ValidationResult
    from schemas.ontology import RefineryOntology
    return "All 9 schemas import OK"


def check_source_modules():
    from src.config import PipelineConfig
    from src.normalizer import DocumentNormalizer
    from src.table_classifier import TableClassifier
    from src.chunker import DocumentChunker
    from src.validator import ExtractionValidator
    from src.checkpoint import PipelineCheckpoint
    from src.reporter import ReportGenerator
    return "Core modules import OK"


def check_ollama():
    import httpx
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=5)
        models = r.json().get("models", [])
        names = [m.get("name", "") for m in models]
        deepseek = [n for n in names if "deepseek" in n.lower()]
        if deepseek:
            return f"Available, DeepSeek models: {deepseek}"
        return f"Available, models: {names[:5]}..."
    except Exception:
        raise RuntimeError("Cannot reach Ollama at localhost:11434")


def check_neo4j_connection():
    from neo4j import GraphDatabase
    from src.config import load_config
    config = load_config()
    driver = GraphDatabase.driver(
        config.neo4j.uri,
        auth=(config.neo4j.username, config.neo4j.password),
    )
    driver.verify_connectivity()
    driver.close()
    return f"Connected to {config.neo4j.uri}"


def check_gpu():
    import subprocess
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
         "--format=csv,noheader"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    raise RuntimeError("nvidia-smi failed")


def check_data_dirs():
    dirs = ["data/raw", "data/parsed", "data/normalized",
            "data/knowledge", "data/checkpoints", "data/reports"]
    missing = [d for d in dirs if not (PROJECT_ROOT / d).exists()]
    if missing:
        raise RuntimeError(f"Missing directories: {missing}")
    pdfs = list((PROJECT_ROOT / "data" / "raw").glob("*.pdf"))
    return f"All dirs OK, {len(pdfs)} PDF(s) in data/raw/"


def main():
    print("=" * 60)
    print("Refinery Knowledge Layer — Environment Validation")
    print("=" * 60)
    
    results = {}
    
    print("\n[Core Dependencies]")
    results["python"] = check("Python", check_python)
    results["pydantic"] = check("Pydantic", check_pydantic)
    results["httpx"] = check("httpx", check_httpx)
    results["rich"] = check("Rich", check_rich)
    results["xxhash"] = check("xxhash", check_xxhash)
    
    print("\n[Heavy Dependencies]")
    results["docling"] = check("Docling", check_docling)
    results["neo4j_driver"] = check("Neo4j driver", check_neo4j)
    results["sentence_transformers"] = check("Sentence Transformers", check_sentence_transformers)
    
    print("\n[Project]")
    results["schemas"] = check("Schemas", check_schemas)
    results["modules"] = check("Source modules", check_source_modules)
    results["data_dirs"] = check("Data directories", check_data_dirs)
    
    print("\n[Services]")
    results["gpu"] = check("GPU", check_gpu)
    results["ollama"] = check("Ollama", check_ollama)
    results["neo4j_conn"] = check("Neo4j connection", check_neo4j_connection)
    
    print("\n" + "=" * 60)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    core_ok = all(results.get(k, False) for k in ["python", "pydantic", "schemas", "modules", "data_dirs"])
    
    print(f"Results: {passed}/{total} checks passed")
    
    if core_ok:
        print("\n[READY] Core project is functional.")
        if not results.get("ollama"):
            print("  ⚠ Ollama not available — extraction phases will be skipped.")
        if not results.get("neo4j_conn"):
            print("  ⚠ Neo4j not connected — graph phases will be skipped.")
        if not results.get("docling"):
            print("  ⚠ Docling not installed — parsing will fail.")
    else:
        print("\n[NOT READY] Core dependencies are missing.")
        print("  Run: pip install -e \".[dev]\"")


if __name__ == "__main__":
    main()
