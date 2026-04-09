import os
from typing import List
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rich.console import Console

try:
    from langchain_huggingface import HuggingFaceEmbeddings as SentenceTransformerEmbeddings
    from langchain_chroma import Chroma
except ImportError:
    from langchain_community.embeddings import SentenceTransformerEmbeddings  # type: ignore[no-redef]
    from langchain_community.vectorstores import Chroma  # type: ignore[no-redef]

from automation.config import load_config

console = Console()

AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "agents")
DB_DIR = os.path.join(os.path.dirname(__file__), "db")


def get_active_agent_names() -> List[str]:
    try:
        config = load_config()
        active_agents = []
        for persona in config.get("personas", []):
            agent_name = Path(persona.get("persona_file", "")).parent.name
            if agent_name and agent_name not in active_agents:
                active_agents.append(agent_name)
        if active_agents:
            return active_agents
    except Exception as e:
        console.print(f"[yellow]Warning: could not load configured personas: {e}[/yellow]")

    return [d for d in os.listdir(AGENTS_DIR) if os.path.isdir(os.path.join(AGENTS_DIR, d))]

def load_documents(agent_name: str) -> List:
    kb_path = os.path.join(AGENTS_DIR, agent_name, "KB")
    docs = []
    if not os.path.exists(kb_path):
        console.print(f"[yellow]Warning: KB directory for {agent_name} not found.[/yellow]")
        return []

    for root, _, files in os.walk(kb_path):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                if file.endswith(".pdf"):
                    loader = PyPDFLoader(file_path)
                    docs.extend(loader.load())
                elif file.endswith(".md"):
                    loader = TextLoader(file_path, encoding="utf-8")
                    docs.extend(loader.load())
                elif file.endswith(".txt"):
                    loader = TextLoader(file_path)
                    docs.extend(loader.load())
            except Exception as e:
                console.print(f"[red]Error loading {file}: {e}[/red]")
    return docs

def ingest_agent_kb(agent_name: str):
    console.print(f"[bold blue]Ingesting KB for {agent_name}...[/bold blue]")
    docs = load_documents(agent_name)
    if not docs:
        console.print(f"[yellow]No documents found for {agent_name}.[/yellow]")
        return

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    splits = text_splitter.split_documents(docs)

    embedding_function = SentenceTransformerEmbeddings(model_name="all-MiniLM-L6-v2")
    
    # Create/Update vector db for specific agent
    persist_directory = os.path.join(DB_DIR, agent_name)
    
    Chroma.from_documents(
        documents=splits,
        embedding=embedding_function,
        persist_directory=persist_directory
    )
    console.print(f"[green]Successfully ingested {len(splits)} chunks for {agent_name}.[/green]")

def main():
    agents = get_active_agent_names()
    for agent in agents:
        if agent != "__pycache__":
            ingest_agent_kb(agent)

if __name__ == "__main__":
    main()
