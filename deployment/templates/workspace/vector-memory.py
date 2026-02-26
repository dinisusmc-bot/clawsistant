#!/usr/bin/env python3
"""Vector memory module — long-term semantic memory using pgvector + fastembed.

Stores memories with 384-dim embeddings (bge-small-en-v1.5) in PostgreSQL.
Supports storing, searching, and recalling memories by semantic similarity.

Categories: conversation, lesson, note, bookmark, fact, preference, project
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── DB config ──────────────────────────────────────────────────────────────
POSTGRES_HOST = os.environ.get("OPENCLAW_POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("OPENCLAW_POSTGRES_PORT", "5433")
POSTGRES_DB = os.environ.get("OPENCLAW_POSTGRES_DB", "openclaw")
POSTGRES_USER = os.environ.get("OPENCLAW_POSTGRES_USER", "openclaw")
POSTGRES_PASSWORD = os.environ.get("OPENCLAW_POSTGRES_PASSWORD", "openclaw_dev_pass")

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

# Lazy-loaded embedding model
_embed_model = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from fastembed import TextEmbedding
        _embed_model = TextEmbedding(EMBEDDING_MODEL)
    return _embed_model


def embed_text(text: str) -> list[float]:
    """Generate embedding vector for a text string."""
    model = _get_embed_model()
    vectors = list(model.embed([text]))
    return vectors[0].tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embedding vectors for multiple texts (batched)."""
    if not texts:
        return []
    model = _get_embed_model()
    return [v.tolist() for v in model.embed(texts)]


def _run_sql(query: str, params: dict | None = None) -> str:
    """Run a SQL query via psql and return stdout."""
    env = {
        **os.environ,
        "PGPASSWORD": POSTGRES_PASSWORD,
    }
    cmd = [
        "psql", "-h", POSTGRES_HOST, "-p", POSTGRES_PORT,
        "-U", POSTGRES_USER, "-d", POSTGRES_DB,
        "-t", "-A", "-F", "\x1f",  # unit separator — won't appear in content
        "-c", query,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env
        )
        return result.stdout.strip()
    except Exception as exc:
        print(f"[{datetime.utcnow().isoformat()}] vector-memory sql error: {exc}")
        return ""


SEP = "\x1f"  # field separator for SQL output parsing


def _format_vector(vec: list[float]) -> str:
    """Format a vector for SQL insertion."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


# ── Core API ───────────────────────────────────────────────────────────────

def store(content: str, category: str = "general", source: str = "",
          metadata: dict | None = None) -> int | None:
    """Store a memory with its embedding. Returns the memory ID."""
    if not content.strip():
        return None

    vec = embed_text(content)
    vec_str = _format_vector(vec)
    meta_json = json.dumps(metadata or {}).replace("'", "''")
    content_escaped = content.replace("'", "''")
    source_escaped = source.replace("'", "''")

    result = _run_sql(
        f"INSERT INTO memories (content, category, source, metadata, embedding) "
        f"VALUES ('{content_escaped}', '{category}', '{source_escaped}', "
        f"'{meta_json}'::jsonb, '{vec_str}'::vector) "
        f"RETURNING id;"
    )
    try:
        return int(result.strip())
    except (ValueError, TypeError):
        return None


def store_batch(items: list[dict]) -> list[int]:
    """Store multiple memories. Each item: {content, category, source, metadata}.
    Returns list of IDs."""
    if not items:
        return []
    texts = [item["content"] for item in items]
    vectors = embed_texts(texts)
    ids = []
    for item, vec in zip(items, vectors):
        vec_str = _format_vector(vec)
        content_escaped = item["content"].replace("'", "''")
        category = item.get("category", "general")
        source = item.get("source", "").replace("'", "''")
        meta_json = json.dumps(item.get("metadata", {})).replace("'", "''")

        result = _run_sql(
            f"INSERT INTO memories (content, category, source, metadata, embedding) "
            f"VALUES ('{content_escaped}', '{category}', '{source}', "
            f"'{meta_json}'::jsonb, '{vec_str}'::vector) "
            f"RETURNING id;"
        )
        try:
            ids.append(int(result.strip()))
        except (ValueError, TypeError):
            pass
    return ids


def search(query: str, limit: int = 5, category: str | None = None,
           min_similarity: float = 0.3) -> list[dict]:
    """Search memories by semantic similarity. Returns list of matches."""
    vec = embed_text(query)
    vec_str = _format_vector(vec)

    where = ""
    if category:
        where = f"AND category = '{category}'"

    result = _run_sql(
        f"SELECT id, REPLACE(content, E'\\n', ' '), category, source, "
        f"REPLACE(metadata::text, E'\\n', ' '), "
        f"1 - (embedding <=> '{vec_str}'::vector) AS similarity, "
        f"created_at::text "
        f"FROM memories "
        f"WHERE 1 - (embedding <=> '{vec_str}'::vector) >= {min_similarity} {where} "
        f"ORDER BY embedding <=> '{vec_str}'::vector "
        f"LIMIT {limit};"
    )

    if not result:
        return []

    memories = []
    for line in result.splitlines():
        parts = line.split(SEP, 6)
        if len(parts) < 7:
            continue
        try:
            meta = json.loads(parts[4]) if parts[4] else {}
        except json.JSONDecodeError:
            meta = {}
        memories.append({
            "id": int(parts[0]),
            "content": parts[1],
            "category": parts[2],
            "source": parts[3],
            "metadata": meta,
            "similarity": round(float(parts[5]), 4),
            "created_at": parts[6],
        })
    return memories


def recall(query: str, limit: int = 5, category: str | None = None) -> str:
    """Search and return a formatted context string for the AI."""
    results = search(query, limit=limit, category=category, min_similarity=0.25)
    if not results:
        return ""
    lines = ["Relevant memories:"]
    for m in results:
        sim_pct = int(m["similarity"] * 100)
        cat = m["category"]
        content = m["content"][:300]
        if len(m["content"]) > 300:
            content += "..."
        lines.append(f"  [{cat}] ({sim_pct}% match) {content}")
    return "\n".join(lines)


def get_by_id(memory_id: int) -> dict | None:
    """Get a specific memory by ID."""
    result = _run_sql(
        f"SELECT id, REPLACE(content, E'\\n', ' '), category, source, "
        f"REPLACE(metadata::text, E'\\n', ' '), created_at::text "
        f"FROM memories WHERE id = {memory_id};"
    )
    if not result:
        return None
    parts = result.split(SEP, 5)
    if len(parts) < 6:
        return None
    try:
        meta = json.loads(parts[4]) if parts[4] else {}
    except json.JSONDecodeError:
        meta = {}
    return {
        "id": int(parts[0]),
        "content": parts[1],
        "category": parts[2],
        "source": parts[3],
        "metadata": meta,
        "created_at": parts[5],
    }


def delete(memory_id: int) -> bool:
    """Delete a memory by ID."""
    result = _run_sql(
        f"WITH deleted AS (DELETE FROM memories WHERE id = {memory_id} RETURNING id) "
        f"SELECT COUNT(*) FROM deleted;"
    )
    return result.strip() == "1"


def count(category: str | None = None) -> int:
    """Count memories, optionally by category."""
    where = f"WHERE category = '{category}'" if category else ""
    result = _run_sql(f"SELECT COUNT(*) FROM memories {where};")
    try:
        return int(result.strip())
    except (ValueError, TypeError):
        return 0


def categories() -> list[dict]:
    """Get category counts."""
    result = _run_sql(
        "SELECT category, COUNT(*) FROM memories GROUP BY category ORDER BY count DESC;"
    )
    if not result:
        return []
    cats = []
    for line in result.splitlines():
        parts = line.split(SEP, 1)
        if len(parts) == 2:
            cats.append({"category": parts[0], "count": int(parts[1])})
    return cats


def store_conversation(user_text: str, bot_response: str, source: str = "telegram") -> int | None:
    """Store a conversation exchange as a memory."""
    combined = f"User: {user_text}\nAshley: {bot_response[:500]}"
    return store(
        content=combined,
        category="conversation",
        source=source,
        metadata={"user_text": user_text[:200], "timestamp": datetime.utcnow().isoformat()},
    )


def store_lesson(lesson_text: str) -> int | None:
    """Store a lesson learned."""
    return store(content=lesson_text, category="lesson", source="user")


def store_note(note_text: str, date: str = "") -> int | None:
    """Store a note."""
    if not date:
        date = datetime.utcnow().strftime("%Y-%m-%d")
    return store(
        content=note_text,
        category="note",
        source="user",
        metadata={"date": date},
    )


def store_bookmark(url: str, title: str = "", tags: str = "") -> int | None:
    """Store a bookmark."""
    content = f"{title}: {url}" if title else url
    if tags:
        content += f" (tags: {tags})"
    return store(
        content=content,
        category="bookmark",
        source="user",
        metadata={"url": url, "title": title, "tags": tags},
    )


def store_project_context(project: str, context: str) -> int | None:
    """Store project-specific context."""
    content = f"Project {project}: {context}"
    return store(
        content=content,
        category="project",
        source="user",
        metadata={"project": project},
    )


def store_fact(fact: str, source: str = "observed") -> int | None:
    """Store a fact/preference about the user."""
    return store(content=fact, category="fact", source=source)


# ── CLI interface ──────────────────────────────────────────────────────────

def _cli():
    """Simple CLI for testing."""
    if len(sys.argv) < 2:
        print("Usage: vector-memory.py <command> [args]")
        print("Commands: store <text>, search <query>, count, categories, migrate")
        return

    cmd = sys.argv[1].lower()

    if cmd == "store" and len(sys.argv) >= 3:
        text = " ".join(sys.argv[2:])
        mid = store(text)
        print(f"Stored memory #{mid}")

    elif cmd == "search" and len(sys.argv) >= 3:
        query = " ".join(sys.argv[2:])
        results = search(query, limit=5)
        if not results:
            print("No matching memories.")
        else:
            for m in results:
                sim = int(m["similarity"] * 100)
                print(f"#{m['id']} [{m['category']}] ({sim}%) {m['content'][:120]}")

    elif cmd == "recall" and len(sys.argv) >= 3:
        query = " ".join(sys.argv[2:])
        print(recall(query))

    elif cmd == "count":
        cat = sys.argv[2] if len(sys.argv) >= 3 else None
        print(f"Total memories: {count(cat)}")

    elif cmd == "categories":
        for c in categories():
            print(f"  {c['category']}: {c['count']}")

    elif cmd == "migrate":
        _migrate_existing_data()

    else:
        print(f"Unknown command: {cmd}")


def _migrate_existing_data():
    """Migrate existing file-based memory to vector DB."""
    migrated = 0

    # 1. Migrate lessons
    lessons_file = Path.home() / ".openclaw" / "workspace" / "agent-context" / "lessons.log"
    if lessons_file.exists():
        for line in lessons_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                store_lesson(line)
                migrated += 1
        print(f"Migrated {migrated} lessons")

    # 2. Migrate bookmarks
    bookmarks_file = Path.home() / ".openclaw" / "workspace" / "agent-context" / "bookmarks.json"
    if bookmarks_file.exists():
        try:
            bookmarks = json.loads(bookmarks_file.read_text())
            bm_count = 0
            for bm in bookmarks:
                store_bookmark(
                    url=bm.get("url", ""),
                    title=bm.get("title", ""),
                    tags=",".join(bm.get("tags", [])),
                )
                bm_count += 1
            print(f"Migrated {bm_count} bookmarks")
            migrated += bm_count
        except (json.JSONDecodeError, Exception) as e:
            print(f"Error migrating bookmarks: {e}")

    # 3. Migrate project contexts
    projects_dir = Path.home() / ".openclaw" / "workspace" / "agent-context" / "projects"
    if projects_dir.exists():
        proj_count = 0
        for f in projects_dir.iterdir():
            if f.is_file():
                content = f.read_text().strip()
                if content:
                    store_project_context(f.stem, content)
                    proj_count += 1
        print(f"Migrated {proj_count} project contexts")
        migrated += proj_count

    # 4. Migrate notes
    notes_dir = Path.home() / ".openclaw" / "workspace" / "notes"
    if notes_dir.exists():
        note_count = 0
        for f in sorted(notes_dir.iterdir()):
            if f.is_file() and f.suffix == ".md":
                content = f.read_text().strip()
                if content:
                    # Date from filename (YYYY-MM-DD.md)
                    date = f.stem if len(f.stem) == 10 else ""
                    store_note(content, date=date)
                    note_count += 1
        print(f"Migrated {note_count} notes")
        migrated += note_count

    # 5. Migrate conversation buffer
    conv_file = Path.home() / ".openclaw" / "workspace" / ".conversation-buffer.json"
    if conv_file.exists():
        try:
            convs = json.loads(conv_file.read_text())
            conv_count = 0
            # Process in pairs (user + ashley)
            i = 0
            while i < len(convs):
                msg = convs[i]
                if msg.get("role") == "user":
                    user_text = msg.get("text", "")
                    bot_text = ""
                    if i + 1 < len(convs) and convs[i + 1].get("role") == "ashley":
                        bot_text = convs[i + 1].get("text", "")
                        i += 1
                    if user_text:
                        store_conversation(user_text, bot_text)
                        conv_count += 1
                elif msg.get("role") == "ashley":
                    # Solo bot message (e.g., scheduled reports)
                    store(
                        content=msg.get("text", "")[:500],
                        category="conversation",
                        source="scheduled",
                        metadata={"role": "ashley", "timestamp": msg.get("ts", "")},
                    )
                    conv_count += 1
                i += 1
            print(f"Migrated {conv_count} conversations")
            migrated += conv_count
        except (json.JSONDecodeError, Exception) as e:
            print(f"Error migrating conversations: {e}")

    print(f"\nTotal migrated: {migrated}")
    print(f"Total memories in DB: {count()}")
    for c in categories():
        print(f"  {c['category']}: {c['count']}")


if __name__ == "__main__":
    _cli()
