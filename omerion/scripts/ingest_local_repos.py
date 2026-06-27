"""Ingest local GitHub repositories into Pinecone for RAG.

Usage:
    cd omerion && uv run python -m scripts.ingest_local_repos
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from omerion_core.logging import get_logger
from pipeline.chunker import DocumentChunker
from pipeline.embedder import EmbeddingGenerator
from pipeline.upserter import VectorStoreUpserter

log = get_logger("scripts.ingest_local_repos")

# Add or remove paths as needed
REPOSITORIES = [
    "/Users/evy/Desktop/OMERION AI EMPLOYEES/obsidian",
    "/Users/evy/Desktop/OMERION AI EMPLOYEES/claude-mem",
    "/Users/evy/Desktop/OMERION AI EMPLOYEES/claude-code-memory-setup",
]

ALLOWED_EXTENSIONS = {
    ".md", ".py", ".js", ".ts", ".jsx", ".tsx", 
    ".json", ".yaml", ".yml", ".txt", ".sql", ".csv"
}

IGNORE_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", ".next", "dist", "build"
}

def main() -> None:
    log.info("Starting local repo ingestion")
    
    chunker = DocumentChunker()
    embedder = EmbeddingGenerator()
    upserter = VectorStoreUpserter()
    
    total_files = 0
    total_chunks = 0
    
    for repo_path in REPOSITORIES:
        repo_dir = Path(repo_path)
        if not repo_dir.exists() or not repo_dir.is_dir():
            log.warning(f"Repository path does not exist: {repo_path}")
            continue
            
        repo_name = repo_dir.name
        log.info(f"Scanning repository: {repo_name}")
        
        for root, dirs, files in os.walk(repo_dir):
            # Mutate dirs in-place to skip ignored directories
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            
            for file_name in files:
                file_path = Path(root) / file_name
                
                if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                    
                # Read file content
                try:
                    text = file_path.read_text(encoding="utf-8")
                except Exception as e:
                    log.warning(f"Could not read {file_path}: {e}")
                    continue
                    
                if not text.strip():
                    continue
                    
                log.info(f"Processing: {file_path}")
                total_files += 1
                
                # Build metadata
                base_metadata = {
                    "file_id": str(file_path),
                    "file_name": file_name,
                    "folder_name": repo_name,
                    "mime_type": "text/plain",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "source_url": f"file://{file_path}",
                    "persona": "knowledge_base",
                    "market": "general",
                    "agent_type": "code_memory",
                    "content_date": datetime.now(timezone.utc).isoformat()[:10],
                }
                
                # Chunk, Embed, Upsert
                try:
                    chunks = chunker.chunk(text, base_metadata)
                    if chunks:
                        chunks = embedder.embed(chunks)
                        upserter.upsert(chunks)
                        total_chunks += len(chunks)
                except Exception as exc:
                    log.error(f"Failed to process {file_name}: {exc}")
                    
    log.info(f"Ingestion complete. Processed {total_files} files into {total_chunks} vector chunks.")

if __name__ == "__main__":
    main()
