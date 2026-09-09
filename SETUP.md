# Initial Setup Guide

This guide provides the necessary steps to set up the `refinery-knowledge-layer` project.

## Prerequisites

- Python >= 3.10
- Neo4j database (Local or Aura)

## Installation

1. **Clone the repository** (if you haven't already):
   ```bash
   git clone <repository-url>
   cd Refinery_KL_Extraction
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv .venv
   ```

3. **Activate the virtual environment**:
   - On Windows:
     ```bash
     .venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```bash
     source .venv/bin/activate
     ```

4. **Install dependencies**:
   The project uses `pyproject.toml` for managing dependencies. Install the package in editable mode along with development dependencies:
   ```bash
   pip install -e .[dev]
   ```

   This will install all required libraries including:
   - `docling` (document parsing)
   - `neo4j` (database driver)
   - `pydantic` (data validation)
   - `sentence-transformers` (embeddings)
   - `onnxruntime`
   - `pytest` (for development/testing)

## Configuration

1. **Neo4j Setup**:
   Ensure you have a running instance of Neo4j. Set the appropriate environment variables or update the configuration file with your database URI, username, and password before running the pipeline.

## Running

You can now run the scripts or start the pipeline. See the `README.md` or `walkthrough.md` for more detailed usage instructions.
