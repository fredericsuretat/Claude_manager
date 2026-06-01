"""Memory Explorer — explore et édite tous les .md de:

  • ~/.claude/memory/                (mémoire globale CC)
  • ~/.claude/projects/*/memory/     (mémoire par-projet CC)
  • ~/Documents/Dev/*  + ~/Documents/Docker  (.md des repos: README, CLAUDE.md, docs/...)

Sécurité: chaque fichier est identifié par (root_id, rel_path).
rel_path est résolu et doit rester strictement à l'intérieur du root.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

HOME = Path.home()
GLOBAL_MEMORY = HOME / ".claude" / "memory"
PROJECTS_DIR = HOME / ".claude" / "projects"

# Racines de repos à scanner récursivement.
REPO_ROOTS: list[Path] = [
    HOME / "Documents" / "Docker",
    HOME / "Documents" / "Dev",
]

# Dossiers à ignorer pendant le scan récursif.
IGNORE_DIRS = {
    "node_modules", ".git", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    "dist", "build", ".next", ".nuxt", "out",
    ".cache", ".turbo", "target", ".idea", ".vscode",
    "_backups", "backups",
}

MAX_FILE_SIZE = 512 * 1024  # 512 KiB max — au-delà on évite (rare pour des .md)

# Match `[label](file.md)` et `[label](./dir/file.md)` — pas les URLs.
_LINK_RE = re.compile(r"\[[^\]]+\]\((?!https?://)([^)\s#]+\.md)(?:#[^)]*)?\)")

# Frontmatter YAML simple: --- ... ---
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# Heading markdown: # / ## / ### …
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
# Index hook: "- [Title](file.md) — description"
_INDEX_LINE_RE = re.compile(
    r"^\s*[-*]\s*\[([^\]]+)\]\(([^)]+\.md)\)\s*[—\-–:]?\s*(.*)$"
)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extrait la frontmatter YAML (simpliste: key: value) + retourne (meta, body)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    meta = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def _extract_headings(body: str) -> list[dict]:
    """Liste tous les headings du corps avec leur niveau."""
    out = []
    for m in _HEADING_RE.finditer(body):
        out.append({
            "level": len(m.group(1)),
            "title": m.group(2).strip(),
            "offset": m.start(),
        })
    return out


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s)
    return s.strip("-")


@dataclass
class MemRoot:
    id: str
    label: str
    path: Path
    kind: str          # "claude" | "repo_parent" | "repo"
    recursive: bool    # claude=False (.md à la racine), repo=True


def _project_label(dirname: str) -> str:
    parts = [p for p in dirname.split("-") if p]
    return parts[-1] if parts else dirname


def discover_roots() -> list[MemRoot]:
    roots: list[MemRoot] = []

    # 1. Mémoire globale CC
    if GLOBAL_MEMORY.is_dir():
        roots.append(MemRoot(
            id="claude:global",
            label="CC · Global",
            path=GLOBAL_MEMORY,
            kind="claude",
            recursive=False,
        ))

    # 2. Mémoire par-projet CC
    if PROJECTS_DIR.is_dir():
        for proj in sorted(PROJECTS_DIR.iterdir()):
            mem = proj / "memory"
            if mem.is_dir():
                roots.append(MemRoot(
                    id=f"claude:proj:{proj.name}",
                    label=f"CC · {_project_label(proj.name)}",
                    path=mem,
                    kind="claude",
                    recursive=False,
                ))

    # 3. Repos — chaque sous-dossier de premier niveau qui contient au moins un .md
    seen_paths = set()
    for parent in REPO_ROOTS:
        if not parent.is_dir():
            continue
        # le parent lui-même peut être un repo (ex: ~/Documents/Docker)
        if (parent / ".git").is_dir() and parent not in seen_paths:
            roots.append(MemRoot(
                id=f"repo:{parent.name}",
                label=f"📁 {parent.name}",
                path=parent,
                kind="repo",
                recursive=True,
            ))
            seen_paths.add(parent)
            continue
        # sinon on liste ses enfants
        for child in sorted(parent.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name in IGNORE_DIRS:
                continue
            if child in seen_paths:
                continue
            # ne garde que si le repo contient au moins un .md (rapide)
            try:
                has_md = any(child.glob("*.md")) or any(child.glob("**/*.md"))
            except OSError:
                has_md = False
            if not has_md:
                continue
            roots.append(MemRoot(
                id=f"repo:{child.name}",
                label=f"📁 {child.name}",
                path=child,
                kind="repo",
                recursive=True,
            ))
            seen_paths.add(child)

    return roots


def _iter_md(root: MemRoot) -> Iterable[Path]:
    """Itère les .md d'un root, en respectant recursive/IGNORE_DIRS."""
    if not root.recursive:
        for f in sorted(root.path.glob("*.md")):
            yield f
        return
    # Walk récursif manuel pour pouvoir ignorer des dossiers
    stack = [root.path]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        # Trie pour ordre stable
        entries.sort(key=lambda p: p.name)
        for entry in entries:
            if entry.is_dir():
                if entry.name in IGNORE_DIRS or entry.name.startswith("."):
                    continue
                stack.append(entry)
            elif entry.is_file() and entry.suffix.lower() == ".md":
                yield entry


class MemoryExplorerService:
    def __init__(self):
        self._roots_cache: list[MemRoot] | None = None
        self._roots_cache_ts: float = 0.0

    # ── Roots ─────────────────────────────────────────────────────
    def _roots(self) -> list[MemRoot]:
        # cache 30s pour éviter de rescanner à chaque requête
        now = time.time()
        if self._roots_cache is None or now - self._roots_cache_ts > 30:
            self._roots_cache = discover_roots()
            self._roots_cache_ts = now
        return self._roots_cache

    def _root_by_id(self, root_id: str) -> MemRoot | None:
        for r in self._roots():
            if r.id == root_id:
                return r
        return None

    def invalidate_cache(self):
        self._roots_cache = None

    def _resolve(self, root_id: str, rel_path: str) -> tuple[MemRoot, Path]:
        root = self._root_by_id(root_id)
        if not root:
            raise ValueError(f"Unknown root: {root_id}")
        if ".." in rel_path.split("/") or rel_path.startswith("/") or "\\" in rel_path:
            raise ValueError(f"Invalid path: {rel_path}")
        if not rel_path.endswith(".md"):
            raise ValueError("Only .md files are allowed")
        target = (root.path / rel_path).resolve()
        root_resolved = root.path.resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError as e:
            raise ValueError("Path escapes root") from e
        return root, target

    # ── Tree ──────────────────────────────────────────────────────
    def tree(self, include_empty: bool = False) -> dict:
        out = []
        for root in self._roots():
            files = []
            for f in _iter_md(root):
                try:
                    st = f.stat()
                except OSError:
                    continue
                rel = str(f.relative_to(root.path))
                files.append({
                    "rel": rel,
                    "name": f.name,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "is_index": f.name in ("MEMORY.md", "CLAUDE.md", "README.md"),
                })
            if not files and not include_empty:
                continue
            files.sort(key=lambda x: x["rel"].lower())
            out.append({
                "id": root.id,
                "label": root.label,
                "path": str(root.path),
                "kind": root.kind,
                "files": files,
                "count": len(files),
            })
        return {"roots": out}

    # ── Read / Write / Create / Delete ────────────────────────────
    def read(self, root_id: str, rel_path: str) -> dict:
        _, path = self._resolve(root_id, rel_path)
        if not path.exists():
            return {"error": "Not found", "root": root_id, "rel": rel_path}
        if path.stat().st_size > MAX_FILE_SIZE:
            return {"error": "File too large", "size": path.stat().st_size}
        content = path.read_text(encoding="utf-8", errors="replace")
        st = path.stat()
        return {
            "root": root_id,
            "rel": rel_path,
            "name": path.name,
            "path": str(path),
            "content": content,
            "size": st.st_size,
            "mtime": st.st_mtime,
        }

    def write(self, root_id: str, rel_path: str, content: str) -> dict:
        _, path = self._resolve(root_id, rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        st = path.stat()
        self.invalidate_cache()
        return {"ok": True, "size": st.st_size, "mtime": st.st_mtime}

    def delete(self, root_id: str, rel_path: str) -> dict:
        _, path = self._resolve(root_id, rel_path)
        if path.exists():
            path.unlink()
            self.invalidate_cache()
            return {"ok": True, "deleted": rel_path}
        return {"ok": False, "error": "Not found"}

    def create(self, root_id: str, rel_path: str, content: str = "") -> dict:
        _, path = self._resolve(root_id, rel_path)
        if path.exists():
            return {"ok": False, "error": "Already exists"}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.invalidate_cache()
        return {"ok": True, "size": len(content)}

    # ── Search (full text) ────────────────────────────────────────
    def search(self, query: str, max_results: int = 200, ctx: int = 80) -> dict:
        q = query.strip()
        if not q:
            return {"query": q, "results": []}
        q_lower = q.lower()
        results = []
        for root in self._roots():
            for f in _iter_md(root):
                try:
                    if f.stat().st_size > MAX_FILE_SIZE:
                        continue
                    text = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                low = text.lower()
                idx = low.find(q_lower)
                if idx == -1:
                    continue
                start = max(0, idx - ctx)
                end = min(len(text), idx + len(q) + ctx)
                snippet = text[start:end]
                count = low.count(q_lower)
                line_no = text[:idx].count("\n") + 1
                results.append({
                    "root": root.id,
                    "root_label": root.label,
                    "rel": str(f.relative_to(root.path)),
                    "name": f.name,
                    "line": line_no,
                    "count": count,
                    "snippet": snippet,
                })
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        results.sort(key=lambda r: -r["count"])
        return {"query": q, "count": len(results), "results": results}

    # ── Recent ────────────────────────────────────────────────────
    def recent(self, limit: int = 40) -> dict:
        all_files = []
        for root in self._roots():
            for f in _iter_md(root):
                try:
                    st = f.stat()
                except OSError:
                    continue
                all_files.append({
                    "root": root.id,
                    "root_label": root.label,
                    "rel": str(f.relative_to(root.path)),
                    "name": f.name,
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "age_seconds": time.time() - st.st_mtime,
                })
        all_files.sort(key=lambda r: -r["mtime"])
        return {"files": all_files[:limit]}

    # ── Graph (markdown links entre .md) ──────────────────────────
    def graph(self) -> dict:
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        files_index: dict[str, tuple[MemRoot, Path]] = {}  # node_id -> (root, path)

        for root in self._roots():
            for f in _iter_md(root):
                rel = str(f.relative_to(root.path))
                node_id = f"{root.id}::{rel}"
                files_index[node_id] = (root, f)
                nodes[node_id] = {
                    "id": node_id,
                    "label": f.name,
                    "rel": rel,
                    "root": root.id,
                    "root_label": root.label,
                    "kind": root.kind,
                    "is_index": f.name in ("MEMORY.md", "CLAUDE.md", "README.md"),
                    "in_degree": 0,
                    "out_degree": 0,
                }

        # Index par nom de fichier pour résolution rapide
        by_name: dict[str, list[str]] = {}
        for nid, (root, path) in files_index.items():
            by_name.setdefault(path.name, []).append(nid)

        for src_id, (root, path) in files_index.items():
            try:
                if path.stat().st_size > MAX_FILE_SIZE:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in _LINK_RE.finditer(text):
                target_rel = m.group(1)
                target_name = target_rel.split("/")[-1]
                # 1. Résolution exacte dans le même root
                candidate_id = f"{root.id}::{target_rel}"
                if candidate_id in nodes:
                    target_id = candidate_id
                else:
                    # 2. Fallback: même nom dans n'importe quel root (privilégie même root)
                    candidates = by_name.get(target_name, [])
                    same_root = [c for c in candidates if c.startswith(root.id + "::")]
                    if same_root:
                        target_id = same_root[0]
                    elif candidates:
                        target_id = candidates[0]
                    else:
                        continue
                if target_id == src_id:
                    continue
                edges.append({"from": src_id, "to": target_id})
                nodes[src_id]["out_degree"] += 1
                nodes[target_id]["in_degree"] += 1

        return {"nodes": list(nodes.values()), "edges": edges}

    # ── Skim (peek) ──────────────────────────────────────────────
    def skim(self, root_id: str, rel_path: str, body_lines: int = 8) -> dict:
        """Aperçu ultra-léger d'un .md: frontmatter + headings + N premières
        lignes de contenu. Pensé pour économiser les tokens vs read() complet.
        """
        _, path = self._resolve(root_id, rel_path)
        if not path.exists():
            return {"error": "Not found"}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return {"error": str(e)}
        meta, body = _parse_frontmatter(text)
        headings = _extract_headings(body)
        # Premières lignes de contenu (en sautant les blank et headings)
        preview_lines = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            preview_lines.append(line.rstrip())
            if len(preview_lines) >= body_lines:
                break
        st = path.stat()
        return {
            "root": root_id,
            "rel": rel_path,
            "name": path.name,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "frontmatter": meta,
            "headings": [{"level": h["level"], "title": h["title"]} for h in headings],
            "preview": "\n".join(preview_lines),
            "total_lines": text.count("\n") + 1,
            "approx_tokens_full": len(text) // 4,        # estimation grossière
            "approx_tokens_skim": (len(str(meta)) + sum(len(h["title"]) for h in headings) + len("\n".join(preview_lines))) // 4,
        }

    # ── Search meta-only ─────────────────────────────────────────
    def search_meta(self, query: str, max_results: int = 500) -> dict:
        """Comme search() mais SANS snippet — pour économiser les tokens
        quand on veut juste savoir QUELS fichiers contiennent un mot.
        """
        q = query.strip()
        if not q:
            return {"query": q, "results": []}
        q_lower = q.lower()
        results = []
        for root in self._roots():
            for f in _iter_md(root):
                try:
                    if f.stat().st_size > MAX_FILE_SIZE:
                        continue
                    text = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                low = text.lower()
                idx = low.find(q_lower)
                if idx == -1:
                    continue
                line_no = text[:idx].count("\n") + 1
                count = low.count(q_lower)
                results.append({
                    "root": root.id,
                    "root_label": root.label,
                    "rel": str(f.relative_to(root.path)),
                    "name": f.name,
                    "line": line_no,
                    "count": count,
                })
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        results.sort(key=lambda r: -r["count"])
        return {"query": q, "count": len(results), "results": results}

    # ── Index parser (MEMORY.md) ─────────────────────────────────
    def parse_index(self, root_id: str, name: str = "MEMORY.md") -> dict:
        """Parse un fichier index (MEMORY.md par convention) et renvoie
        la liste structurée des entrées `- [Title](file.md) — hook`.
        """
        try:
            _, path = self._resolve(root_id, name)
        except ValueError as e:
            return {"error": str(e)}
        if not path.exists():
            return {"error": f"Index {name} not found in {root_id}"}
        text = path.read_text(encoding="utf-8", errors="replace")
        entries = []
        missing = []
        for raw_line in text.splitlines():
            m = _INDEX_LINE_RE.match(raw_line)
            if not m:
                continue
            title, file_ref, hook = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            # Le hook référence un fichier — vérifier qu'il existe dans ce root
            try:
                _, target = self._resolve(root_id, file_ref)
                exists = target.exists()
            except ValueError:
                target = None
                exists = False
            entries.append({
                "title": title,
                "file": file_ref,
                "hook": hook,
                "exists": exists,
            })
            if not exists:
                missing.append(file_ref)
        # Détection d'orphelins: .md dans le dossier sans entrée dans l'index
        all_md = set()
        root = self._root_by_id(root_id)
        if root:
            for f in _iter_md(root):
                rel = str(f.relative_to(root.path))
                if rel != name:
                    all_md.add(rel)
        indexed = {e["file"] for e in entries}
        orphans = sorted(all_md - indexed)
        return {
            "root": root_id,
            "index_file": name,
            "entries": entries,
            "count": len(entries),
            "missing": missing,
            "orphans": orphans,
        }

    # ── Section read ─────────────────────────────────────────────
    def read_section(self, root_id: str, rel_path: str, heading: str) -> dict:
        """Renvoie le contenu d'UNE section markdown (du heading `heading`
        jusqu'au prochain heading de niveau ≤ ou la fin du fichier).
        `heading` peut être le titre exact OU son slug.
        """
        _, path = self._resolve(root_id, rel_path)
        if not path.exists():
            return {"error": "Not found"}
        text = path.read_text(encoding="utf-8", errors="replace")
        _, body = _parse_frontmatter(text)
        heading_norm = heading.strip()
        slug = _slugify(heading_norm)
        headings = _extract_headings(body)
        # cherche par match exact (case-insensitive) puis par slug
        match_idx = -1
        for i, h in enumerate(headings):
            if h["title"].lower() == heading_norm.lower() or _slugify(h["title"]) == slug:
                match_idx = i
                break
        if match_idx == -1:
            return {
                "error": f"Heading not found: {heading}",
                "available": [h["title"] for h in headings],
            }
        current = headings[match_idx]
        # Fin de section = prochain heading de niveau ≤ current.level
        end_offset = len(body)
        for next_h in headings[match_idx + 1:]:
            if next_h["level"] <= current["level"]:
                end_offset = next_h["offset"]
                break
        section = body[current["offset"]:end_offset].rstrip()
        return {
            "root": root_id,
            "rel": rel_path,
            "heading": current["title"],
            "level": current["level"],
            "content": section,
            "approx_tokens": len(section) // 4,
            "approx_tokens_full_file": len(text) // 4,
        }

    # ── Search headings (chercher dans les titres) ───────────────
    def search_headings(self, query: str, max_results: int = 200) -> dict:
        """Cherche `query` uniquement dans les headings markdown.
        Permet de répondre "où est documenté X ?" sans lire les contenus.
        """
        q = query.strip().lower()
        if not q:
            return {"query": q, "results": []}
        results = []
        for root in self._roots():
            for f in _iter_md(root):
                try:
                    if f.stat().st_size > MAX_FILE_SIZE:
                        continue
                    text = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                _, body = _parse_frontmatter(text)
                for h in _extract_headings(body):
                    if q in h["title"].lower():
                        results.append({
                            "root": root.id,
                            "root_label": root.label,
                            "rel": str(f.relative_to(root.path)),
                            "name": f.name,
                            "heading": h["title"],
                            "level": h["level"],
                        })
                        if len(results) >= max_results:
                            return {"query": q, "count": len(results), "results": results}
        return {"query": q, "count": len(results), "results": results}

    # ── Stats globales ────────────────────────────────────────────
    def stats(self) -> dict:
        total_files = 0
        total_size = 0
        per_root = []
        for root in self._roots():
            n = 0
            s = 0
            for f in _iter_md(root):
                try:
                    n += 1
                    s += f.stat().st_size
                except OSError:
                    pass
            total_files += n
            total_size += s
            per_root.append({
                "id": root.id, "label": root.label,
                "kind": root.kind, "count": n, "size": s,
            })
        return {
            "total_files": total_files,
            "total_size": total_size,
            "per_root": per_root,
            "roots_count": len(per_root),
        }
