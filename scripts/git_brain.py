#!/usr/bin/env python3
"""
Git-backed memory system for the anti-legacy pipeline.

Stores learnings, decisions, translation patterns, architecture docs, security
rules, and any reference content as files on orphan branches in the project's
git repo. No external services, no npm packages.

Branch naming: brain/anti-legacy/{category}
Categories: learnings, decisions, patterns

Reading is done via `git show` — no checkout needed, working tree stays clean.
Writing checkouts the orphan branch into a temp worktree, commits, and cleans up.

Sync does smart merging: index.json is regenerated deterministically from the
file listing; content files are merged section-by-section, deduplicating and
combining unique content from both sides. Truly irreconcilable conflicts are
surfaced with both versions preserved and a structured report for the agent.
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import shutil
from datetime import datetime, timezone


BRANCH_PREFIX = "brain/anti-legacy"
CATEGORIES = ["learnings", "decisions", "patterns"]

# Content types for richer classification
CONTENT_TYPES = [
    "learning",       # episodic note from a task
    "pattern",        # reusable translation recipe
    "decision",       # architectural or gate decision
    "architecture",   # architecture doc, system design
    "security",       # security rules, policies
    "reference",      # reference material, standards
    "runbook",        # operational procedures
]


def _run_git(*args, cwd=None, check=True, capture=True):
    """Run a git command and return stdout."""
    cmd = ["git"] + list(args)
    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip() if result.stderr else ''}"
        )
    return result


def _git_repo_root(cwd=None):
    """Find the git repo root."""
    result = _run_git("rev-parse", "--show-toplevel", cwd=cwd, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _branch_exists(branch, cwd=None):
    """Check if a local branch exists."""
    result = _run_git("rev-parse", "--verify", branch, cwd=cwd, check=False)
    return result.returncode == 0


def _branch_name(category):
    """Full branch name for a category."""
    return f"{BRANCH_PREFIX}/{category}"


def _read_index(category, cwd=None):
    """Read index.json from a brain branch without checking it out."""
    branch = _branch_name(category)
    result = _run_git("show", f"{branch}:index.json", cwd=cwd, check=False)
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def _read_file_from_branch(category, path, cwd=None):
    """Read a file from a brain branch without checking it out."""
    branch = _branch_name(category)
    result = _run_git("show", f"{branch}:{path}", cwd=cwd, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def _with_worktree(category, fn, cwd=None):
    """
    Checkout a brain branch into a temp worktree, run fn(worktree_path),
    then clean up. Returns whatever fn returns.
    """
    branch = _branch_name(category)
    repo_root = _git_repo_root(cwd) or cwd or os.getcwd()
    worktree_dir = tempfile.mkdtemp(prefix=f"brain-{category}-")

    try:
        _run_git("worktree", "add", "--detach", worktree_dir, branch, cwd=repo_root)
        # Checkout the branch in the worktree
        _run_git("checkout", branch, cwd=worktree_dir)
        result = fn(worktree_dir)
        return result
    finally:
        # Clean up worktree
        try:
            _run_git("worktree", "remove", "--force", worktree_dir, cwd=repo_root)
        except RuntimeError:
            # Fallback: manual cleanup
            shutil.rmtree(worktree_dir, ignore_errors=True)
            try:
                _run_git("worktree", "prune", cwd=repo_root)
            except RuntimeError:
                pass


# ---------------------------------------------------------------------------
# Index rebuilding — the deterministic merge strategy for index.json
# ---------------------------------------------------------------------------

def _rebuild_index_from_files(worktree_path, existing_index=None):
    """
    Rebuild index.json deterministically from the files on disk.
    Preserves metadata (tags, type, created_at) from existing_index where
    the file path matches. New files get default metadata.
    """
    existing_by_path = {}
    if existing_index:
        for entry in existing_index:
            existing_by_path[entry.get("path", "")] = entry

    new_index = []
    for root, dirs, files in os.walk(worktree_path):
        # Skip .git directory
        dirs[:] = [d for d in dirs if d != ".git"]
        for fname in files:
            if fname in ("index.json", "README.md"):
                continue
            rel_path = os.path.relpath(os.path.join(root, fname), worktree_path)
            if rel_path in existing_by_path:
                new_index.append(existing_by_path[rel_path])
            else:
                # New file — extract metadata from content if possible
                entry = {
                    "id": fname.rsplit(".", 1)[0],
                    "path": rel_path,
                    "tags": [],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                # Try to read tags from file header
                try:
                    full = os.path.join(root, fname)
                    with open(full) as f:
                        for line in f:
                            if line.startswith("**Tags**:"):
                                tag_str = line.split(":", 1)[1].strip()
                                entry["tags"] = [t.strip() for t in tag_str.split(",") if t.strip()]
                                break
                            if line.startswith("**Type**:"):
                                entry["type"] = line.split(":", 1)[1].strip()
                except (OSError, UnicodeDecodeError):
                    pass
                new_index.append(entry)

    # Sort by created_at for deterministic output
    new_index.sort(key=lambda e: e.get("created_at", ""))
    return new_index


# ---------------------------------------------------------------------------
# Smart merge — section-level content merging
# ---------------------------------------------------------------------------

def _extract_conflict_sides(content):
    """
    Parse git conflict markers and return list of (local_text, remote_text)
    tuples for each conflict region, plus the non-conflicting parts.
    Returns (merged_possible, result_text) where merged_possible is True if
    we could resolve all conflicts automatically.
    """
    lines = content.split("\n")
    output = []
    in_conflict = False
    local_lines = []
    remote_lines = []
    in_remote = False
    conflicts_found = 0
    conflicts_resolved = 0

    for line in lines:
        if line.startswith("<<<<<<<"):
            in_conflict = True
            in_remote = False
            local_lines = []
            remote_lines = []
            conflicts_found += 1
        elif line.startswith("=======") and in_conflict:
            in_remote = True
        elif line.startswith(">>>>>>>") and in_conflict:
            in_conflict = False
            # Attempt smart merge of this conflict region
            merged = _merge_sections(local_lines, remote_lines)
            if merged is not None:
                output.extend(merged)
                conflicts_resolved += 1
            else:
                # Can't auto-resolve — keep both with clear markers
                output.append("<!-- ⚠️ MERGE CONFLICT — agent review needed -->")
                output.append("<!-- LOCAL VERSION -->")
                output.extend(local_lines)
                output.append("<!-- REMOTE VERSION -->")
                output.extend(remote_lines)
                output.append("<!-- END CONFLICT -->")
        elif in_conflict:
            if in_remote:
                remote_lines.append(line)
            else:
                local_lines.append(line)
        else:
            output.append(line)

    all_resolved = conflicts_found > 0 and conflicts_resolved == conflicts_found
    return all_resolved, "\n".join(output), conflicts_found, conflicts_resolved


def _merge_sections(local_lines, remote_lines):
    """
    Attempt to intelligently merge two sets of lines.

    Strategy:
    1. If one side is empty, take the other
    2. If both are identical, take either
    3. If one is a superset of the other, take the superset
    4. If they're additive (both add unique content), combine and deduplicate
    5. If they truly contradict (same line changed differently), return None
    """
    local_text = "\n".join(local_lines).strip()
    remote_text = "\n".join(remote_lines).strip()

    # Case 1: One side empty
    if not local_text:
        return remote_lines
    if not remote_text:
        return local_lines

    # Case 2: Identical
    if local_text == remote_text:
        return local_lines

    # Case 3: Superset check
    local_set = set(l.strip() for l in local_lines if l.strip())
    remote_set = set(l.strip() for l in remote_lines if l.strip())

    if local_set.issuperset(remote_set):
        return local_lines
    if remote_set.issuperset(local_set):
        return remote_lines

    # Case 4: Additive — lines are all unique additions
    # Check if the diff is only additions (no modifications)
    matcher = difflib.SequenceMatcher(None, local_lines, remote_lines)
    ops = matcher.get_opcodes()

    has_replace = any(tag == "replace" for tag, _, _, _, _ in ops)

    if not has_replace:
        # Pure additions — combine both sides, preserving order
        # Use local as base, insert remote-only lines at appropriate positions
        combined = list(local_lines)
        for tag, i1, i2, j1, j2 in ops:
            if tag == "insert":
                # Find insertion point in combined
                insert_at = i1
                for line in remote_lines[j1:j2]:
                    combined.insert(insert_at, line)
                    insert_at += 1
        return combined

    # Case 4b: Both add unique lines but at overlapping positions
    # Combine unique lines from both, preserving local order first
    only_in_remote = [l for l in remote_lines if l.strip() and l.strip() not in local_set]
    if only_in_remote:
        # Append remote-only content after local content
        combined = list(local_lines)
        combined.append("")  # separator
        combined.extend(only_in_remote)
        return combined

    # Case 5: True contradiction — can't auto-resolve
    return None


def _merge_index_files(local_index, remote_index):
    """
    Merge two index.json arrays. Combine entries, deduplicate by path,
    prefer newer entry when same path exists in both.
    """
    by_path = {}

    # Load local entries first
    for entry in local_index:
        by_path[entry.get("path", "")] = entry

    # Merge remote entries — keep newer if duplicate path
    for entry in remote_index:
        path = entry.get("path", "")
        if path in by_path:
            existing = by_path[path]
            # Keep whichever is newer
            if entry.get("created_at", "") > existing.get("created_at", ""):
                by_path[path] = entry
        else:
            by_path[path] = entry

    result = list(by_path.values())
    result.sort(key=lambda e: e.get("created_at", ""))
    return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args):
    """Create orphan branches for each category if they don't exist."""
    repo_root = _git_repo_root()
    if not repo_root:
        print("Error: Not inside a git repository.", file=sys.stderr)
        sys.exit(1)

    created = []
    skipped = []

    for category in CATEGORIES:
        branch = _branch_name(category)
        if _branch_exists(branch):
            skipped.append(branch)
            continue

        # Create orphan branch with an initial index.json
        tmp_dir = tempfile.mkdtemp(prefix=f"brain-init-{category}-")
        try:
            # Initialize a fresh git directory for the orphan
            _run_git("init", cwd=tmp_dir)
            _run_git("checkout", "--orphan", branch, cwd=tmp_dir)

            # Write initial index
            index_path = os.path.join(tmp_dir, "index.json")
            with open(index_path, "w") as f:
                json.dump([], f, indent=2)

            # Write a README for the branch
            readme_path = os.path.join(tmp_dir, "README.md")
            with open(readme_path, "w") as f:
                f.write(f"# anti-legacy brain: {category}\n\n")
                f.write(f"This orphan branch stores {category} for the anti-legacy pipeline.\n")
                f.write("Do not merge this branch into main.\n")

            _run_git("add", ".", cwd=tmp_dir)
            _run_git(
                "commit", "-m", f"brain: initialize {category} branch",
                cwd=tmp_dir,
            )

            # Fetch the orphan branch into the real repo
            _run_git("fetch", tmp_dir, f"{branch}:{branch}", cwd=repo_root)
            created.append(branch)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if created:
        print(f"Created: {', '.join(created)}")
    if skipped:
        print(f"Already exist: {', '.join(skipped)}")
    if not created and not skipped:
        print("No branches created.")


def cmd_store(args):
    """Store content on an orphan branch — from inline text or a file."""
    category = args.category
    branch = _branch_name(category)
    repo_root = _git_repo_root()

    if not repo_root:
        print("Error: Not inside a git repository.", file=sys.stderr)
        sys.exit(1)

    if not _branch_exists(branch):
        print(f"Error: Branch {branch} does not exist. Run 'init' first.", file=sys.stderr)
        sys.exit(1)

    # Get content from --content or --file
    if args.file:
        if not os.path.exists(args.file):
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        with open(args.file, "r") as f:
            content = f.read()
        # Use the filename as the slug base if no explicit title
        slug_base = os.path.basename(args.file).rsplit(".", 1)[0]
    elif args.content:
        content = args.content
        slug_base = None
    else:
        print("Error: Either --content or --file is required.", file=sys.stderr)
        sys.exit(1)

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    content_type = args.type or ("pattern" if category == "patterns" else
                                  "decision" if category == "decisions" else
                                  "learning")
    now = datetime.now(timezone.utc)
    date_prefix = now.strftime("%Y-%m-%d")

    # Generate filename
    if slug_base:
        slug = re.sub(r"[^a-z0-9-]", "", slug_base.lower().replace("_", "-"))
    else:
        slug = "-".join(tags[:3]) if tags else "note"
        slug = re.sub(r"[^a-z0-9-]", "", slug.lower())
    filename = f"{date_prefix}_{slug}.md"

    # If storing from a file, preserve its extension
    if args.file and not args.file.endswith(".md"):
        ext = os.path.splitext(args.file)[1]
        filename = f"{date_prefix}_{slug}{ext}"

    def _do_store(worktree_path):
        # Read existing index
        index_path = os.path.join(worktree_path, "index.json")
        if os.path.exists(index_path):
            with open(index_path) as f:
                index = json.load(f)
        else:
            index = []

        # Check for duplicate filename, add suffix if needed
        existing_files = {entry["path"] for entry in index}
        final_filename = filename
        counter = 1
        while final_filename in existing_files:
            base, ext = os.path.splitext(filename)
            final_filename = f"{base}-{counter}{ext}"
            counter += 1

        # Handle subdirectory
        if args.subdir:
            os.makedirs(os.path.join(worktree_path, args.subdir), exist_ok=True)
            final_path = os.path.join(args.subdir, final_filename)
        else:
            final_path = final_filename

        # Write the content
        full_path = os.path.join(worktree_path, final_path)
        with open(full_path, "w") as f:
            if args.file:
                # Store file content as-is, but prepend metadata header
                # if it's a markdown file and doesn't already have one
                if final_filename.endswith(".md") and not content.startswith("---"):
                    f.write(f"**Tags**: {', '.join(tags)}\n")
                    f.write(f"**Type**: {content_type}\n")
                    f.write(f"**Created**: {now.isoformat()}\n")
                    if args.title:
                        f.write(f"**Title**: {args.title}\n")
                    f.write("\n---\n\n")
                f.write(content)
            else:
                f.write(f"# {args.title or slug}\n\n")
                f.write(f"**Tags**: {', '.join(tags)}\n")
                f.write(f"**Type**: {content_type}\n")
                f.write(f"**Created**: {now.isoformat()}\n\n")
                f.write(content + "\n")

        # Update index
        entry = {
            "id": final_filename.rsplit(".", 1)[0],
            "path": final_path,
            "tags": tags,
            "type": content_type,
            "created_at": now.isoformat(),
        }
        if args.title:
            entry["title"] = args.title

        # If same path exists (re-store), replace it
        index = [e for e in index if e.get("path") != final_path]
        index.append(entry)

        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)

        # Commit
        _run_git("add", ".", cwd=worktree_path)
        commit_msg = f"brain: store {category}/{final_filename}"
        if args.title:
            commit_msg = f"brain: {content_type} — {args.title}"
        _run_git("commit", "-m", commit_msg, cwd=worktree_path)

        return final_path

    result_path = _with_worktree(category, _do_store, cwd=repo_root)
    print(f"Stored: {branch}:{result_path}")


def cmd_search(args):
    """Search brain by tag overlap and keyword matching."""
    category = args.category or None
    query_terms = args.query.lower().split()
    limit = args.limit
    filter_type = args.type

    categories_to_search = [category] if category else CATEGORIES
    results = []

    for cat in categories_to_search:
        branch = _branch_name(cat)
        if not _branch_exists(branch):
            continue

        index = _read_index(cat)
        for entry in index:
            # Filter by type if specified
            if filter_type and entry.get("type", "") != filter_type:
                continue

            # Score by tag overlap
            entry_tags_lower = [t.lower() for t in entry.get("tags", [])]
            tag_overlap = sum(1 for term in query_terms if term in entry_tags_lower)

            # Score by title match
            title = entry.get("title", "").lower()
            title_hits = sum(1 for term in query_terms if term in title) * 3

            # Score by type match
            type_match = 2 if entry.get("type", "").lower() in query_terms else 0

            # Read content for keyword matching
            content = _read_file_from_branch(cat, entry["path"])
            keyword_hits = 0
            if content:
                content_lower = content.lower()
                keyword_hits = sum(1 for term in query_terms if term in content_lower)

            score = tag_overlap * 2 + keyword_hits + title_hits + type_match
            if score > 0:
                results.append({
                    "category": cat,
                    "id": entry.get("id", ""),
                    "path": entry["path"],
                    "tags": entry.get("tags", []),
                    "type": entry.get("type", ""),
                    "title": entry.get("title", ""),
                    "score": score,
                    "snippet": (content[:200] + "...") if content and len(content) > 200 else content,
                })

    # Sort by score descending
    results.sort(key=lambda r: r["score"], reverse=True)
    results = results[:limit]

    if not results:
        print("No matches found.")
        return

    for r in results:
        type_label = f" [{r['type']}]" if r.get("type") else ""
        title_label = f" — {r['title']}" if r.get("title") else ""
        print(f"[{r['category']}]{type_label} {r['path']}{title_label} (score: {r['score']})")
        if r.get("tags"):
            print(f"  tags: {', '.join(r['tags'])}")
        if r.get("snippet"):
            # Print first non-empty content line
            lines = [l for l in r["snippet"].split("\n")
                     if l.strip() and not l.startswith("#") and not l.startswith("**")]
            if lines:
                print(f"  {lines[0].strip()}")
        print()


def cmd_list(args):
    """List all entries in a brain category."""
    category = args.category or None
    filter_type = args.type
    categories_to_list = [category] if category else CATEGORIES

    for cat in categories_to_list:
        branch = _branch_name(cat)
        if not _branch_exists(branch):
            continue

        index = _read_index(cat)
        if filter_type:
            index = [e for e in index if e.get("type", "") == filter_type]

        if not index:
            continue

        print(f"\n{branch} ({len(index)} entries)")
        print("-" * 50)

        for entry in index:
            type_label = f" [{entry.get('type', '')}]" if entry.get("type") else ""
            title = entry.get("title", "")
            title_label = f" — {title}" if title else ""
            tags = ", ".join(entry.get("tags", []))
            print(f"  {entry['path']}{type_label}{title_label}")
            if tags:
                print(f"    tags: {tags}")

    if not any(_branch_exists(_branch_name(c)) for c in categories_to_list):
        print("No brain branches found. Run 'init' first.")


def cmd_read(args):
    """Read the full content of a brain entry."""
    category = args.category
    path = args.path

    if not _branch_exists(_branch_name(category)):
        print(f"Error: Branch {_branch_name(category)} does not exist.", file=sys.stderr)
        sys.exit(1)

    content = _read_file_from_branch(category, path)
    if content is None:
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    print(content)


def cmd_ingest(args):
    """Copy a file from the working tree into a brain branch."""
    category = args.category
    branch = _branch_name(category)
    repo_root = _git_repo_root()

    if not repo_root:
        print("Error: Not inside a git repository.", file=sys.stderr)
        sys.exit(1)

    if not _branch_exists(branch):
        print(f"Error: Branch {branch} does not exist. Run 'init' first.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.file):
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    with open(args.file, "r") as f:
        content = f.read()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    content_type = args.type or "reference"
    basename = os.path.basename(args.file)

    def _do_ingest(worktree_path):
        # Handle subdirectory
        if args.subdir:
            dest_dir = os.path.join(worktree_path, args.subdir)
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(args.subdir, basename)
        else:
            dest_path = basename

        full_dest = os.path.join(worktree_path, dest_path)
        with open(full_dest, "w") as f:
            f.write(content)

        # Update index
        index_path = os.path.join(worktree_path, "index.json")
        if os.path.exists(index_path):
            with open(index_path) as f:
                index = json.load(f)
        else:
            index = []

        # Remove existing entry for same path if re-ingesting
        index = [e for e in index if e.get("path") != dest_path]

        index.append({
            "id": basename.rsplit(".", 1)[0],
            "path": dest_path,
            "tags": tags,
            "type": content_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)

        _run_git("add", ".", cwd=worktree_path)
        _run_git("commit", "-m", f"brain: ingest {dest_path}", cwd=worktree_path)

        return dest_path

    result = _with_worktree(category, _do_ingest, cwd=repo_root)
    print(f"Ingested: {branch}:{result}")


def cmd_sync(args):
    """
    Push/pull brain branches to/from remote with smart merging.

    Merge strategy:
    - index.json: regenerate deterministically from the file listing,
      combining metadata from both sides (prefer newer for duplicates)
    - Content files (.md, etc.): section-level merge — combine unique
      content, deduplicate, preserve both sides' additions
    - Irreconcilable conflicts: preserve both versions with clear markers
      and output a structured report for agent review
    """
    repo_root = _git_repo_root()
    if not repo_root:
        print("Error: Not inside a git repository.", file=sys.stderr)
        sys.exit(1)

    # Check if remote exists
    result = _run_git("remote", check=False)
    if result.returncode != 0 or not result.stdout.strip():
        print("No remote configured. Brain is local-only.")
        return

    remote = result.stdout.strip().split("\n")[0]
    synced = []
    failed = []
    merge_reports = []

    for category in CATEGORIES:
        branch = _branch_name(category)
        if not _branch_exists(branch):
            continue

        remote_ref = f"{remote}/{branch}"

        # Fetch remote branch
        fetch_result = _run_git(
            "fetch", remote, f"refs/heads/{branch}:refs/remotes/{remote_ref}",
            cwd=repo_root, check=False
        )

        if fetch_result.returncode != 0:
            # Remote branch doesn't exist yet — just push
            push_result = _run_git("push", remote, branch, cwd=repo_root, check=False)
            if push_result.returncode == 0:
                synced.append(branch)
            else:
                failed.append((branch, push_result.stderr.strip() if push_result.stderr else "push failed"))
            continue

        # Check if merge is needed
        merge_base = _run_git(
            "merge-base", branch, remote_ref, cwd=repo_root, check=False
        )
        local_head = _run_git("rev-parse", branch, cwd=repo_root, check=False)
        remote_head = _run_git("rev-parse", remote_ref, cwd=repo_root, check=False)

        if local_head.stdout.strip() == remote_head.stdout.strip():
            # Already in sync
            synced.append(branch)
            continue

        # Attempt merge in a worktree
        def _do_smart_merge(worktree_path):
            merge_result = _run_git(
                "merge", remote_ref, "--no-edit",
                cwd=worktree_path, check=False
            )

            if merge_result.returncode == 0:
                # Clean merge — no conflicts
                return {"status": "clean", "conflicts": []}

            # Conflicts detected — smart merge time
            conflict_report = {
                "status": "conflicts",
                "branch": branch,
                "conflicts": [],
                "auto_resolved": [],
                "needs_review": [],
            }

            # Find conflicting files
            status = _run_git("diff", "--name-only", "--diff-filter=U",
                              cwd=worktree_path, check=False)
            conflicting_files = [f for f in status.stdout.strip().split("\n") if f]

            for conf_file in conflicting_files:
                full_path = os.path.join(worktree_path, conf_file)
                if not os.path.exists(full_path):
                    continue

                with open(full_path, "r") as f:
                    conflicted_content = f.read()

                if conf_file == "index.json":
                    # Smart merge: rebuild from both sides
                    local_idx = _read_index(category, cwd=repo_root)
                    remote_result = _run_git(
                        "show", f"{remote_ref}:index.json",
                        cwd=repo_root, check=False
                    )
                    remote_idx = []
                    if remote_result.returncode == 0:
                        try:
                            remote_idx = json.loads(remote_result.stdout)
                        except json.JSONDecodeError:
                            pass

                    merged_index = _merge_index_files(local_idx, remote_idx)

                    # Also rebuild from files on disk to catch any new files
                    merged_index = _rebuild_index_from_files(
                        worktree_path, existing_index=merged_index
                    )

                    with open(full_path, "w") as f:
                        json.dump(merged_index, f, indent=2)

                    _run_git("add", conf_file, cwd=worktree_path)
                    conflict_report["auto_resolved"].append(conf_file)

                else:
                    # Content file — attempt section-level merge
                    resolved, merged_text, total, auto = _extract_conflict_sides(
                        conflicted_content
                    )

                    with open(full_path, "w") as f:
                        f.write(merged_text)

                    if resolved:
                        _run_git("add", conf_file, cwd=worktree_path)
                        conflict_report["auto_resolved"].append(conf_file)
                    else:
                        # Partially resolved — mark as needing review
                        _run_git("add", conf_file, cwd=worktree_path)
                        conflict_report["needs_review"].append({
                            "file": conf_file,
                            "total_conflicts": total,
                            "auto_resolved": auto,
                            "remaining": total - auto,
                        })

            # Commit the merge resolution
            if conflict_report["auto_resolved"] or conflict_report["needs_review"]:
                _run_git(
                    "commit", "--no-edit", "-m",
                    f"brain: smart merge {branch} ({len(conflict_report['auto_resolved'])} auto-resolved)",
                    cwd=worktree_path, check=False
                )

            return conflict_report

        try:
            report = _with_worktree(category, _do_smart_merge, cwd=repo_root)
        except RuntimeError as e:
            failed.append((branch, str(e)))
            continue

        if report["status"] == "clean" or (
            report.get("auto_resolved") and not report.get("needs_review")
        ):
            # Push after clean/auto-resolved merge
            push_result = _run_git("push", remote, branch, cwd=repo_root, check=False)
            if push_result.returncode == 0:
                synced.append(branch)
                if report.get("auto_resolved"):
                    print(f"  ✅ {branch}: {len(report['auto_resolved'])} conflicts auto-resolved")
            else:
                failed.append((branch, "merge succeeded but push failed"))
        else:
            merge_reports.append(report)

    # Print results
    if synced:
        print(f"Synced: {', '.join(synced)}")

    if merge_reports:
        print("\n" + "=" * 60)
        print("⚠️  MERGE CONFLICTS NEED AGENT REVIEW")
        print("=" * 60)
        for report in merge_reports:
            print(f"\nBranch: {report['branch']}")
            if report.get("auto_resolved"):
                print(f"  Auto-resolved: {', '.join(report['auto_resolved'])}")
            for item in report.get("needs_review", []):
                print(f"  ⚠️  {item['file']}: {item['remaining']} conflict(s) need review")
                print(f"      ({item['auto_resolved']}/{item['total_conflicts']} auto-resolved)")
            print(f"\n  To review, run:")
            print(f"    python3 scripts/git_brain.py read --category {report['branch'].split('/')[-1]} --path <file>")
            print(f"  Look for '<!-- ⚠️ MERGE CONFLICT -->' markers")
            print(f"  After resolving, run: python3 scripts/git_brain.py sync")

    if failed:
        for branch, err in failed:
            print(f"\n⚠️  SYNC FAILED for {branch}: {err}")
            print(f"    Run: git fetch {remote} && python3 scripts/git_brain.py sync")
            print(f"    This will NOT lose data — both versions are preserved.")


def cmd_status(args):
    """Show brain branch status."""
    repo_root = _git_repo_root()
    if not repo_root:
        print("Error: Not inside a git repository.", file=sys.stderr)
        sys.exit(1)

    print("Git Brain Status")
    print("=" * 40)

    for category in CATEGORIES:
        branch = _branch_name(category)
        exists = _branch_exists(branch)

        if not exists:
            print(f"  {branch}: NOT INITIALIZED")
            continue

        # Count commits
        log_result = _run_git(
            "rev-list", "--count", branch, check=False
        )
        commit_count = log_result.stdout.strip() if log_result.returncode == 0 else "?"

        # Count index entries
        index = _read_index(category)
        entry_count = len(index)

        # Count by type
        type_counts = {}
        for entry in index:
            t = entry.get("type", "untyped")
            type_counts[t] = type_counts.get(t, 0) + 1

        # Last commit date
        date_result = _run_git(
            "log", "-1", "--format=%ci", branch, check=False
        )
        last_date = date_result.stdout.strip() if date_result.returncode == 0 else "?"

        print(f"  {branch}: {entry_count} entries, {commit_count} commits, last: {last_date}")
        if type_counts:
            type_summary = ", ".join(f"{v} {k}" for k, v in sorted(type_counts.items()))
            print(f"    types: {type_summary}")


def main():
    parser = argparse.ArgumentParser(
        prog="git-brain",
        description="Git-backed memory for the anti-legacy pipeline. "
                    "Stores learnings, architecture docs, patterns, security rules "
                    "on orphan branches — no external dependencies.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_p = subparsers.add_parser("init", help="Create orphan branches for brain storage")
    init_p.set_defaults(func=cmd_init)

    # store
    store_p = subparsers.add_parser("store", help="Store content on a brain branch")
    store_grp = store_p.add_mutually_exclusive_group(required=True)
    store_grp.add_argument("--content", help="Inline content to store")
    store_grp.add_argument("--file", help="File to store (preserves content, adds metadata header)")
    store_p.add_argument("--tags", required=True, help="Comma-separated tags")
    store_p.add_argument("--category", default="learnings", choices=CATEGORIES)
    store_p.add_argument("--type", choices=CONTENT_TYPES,
                         help="Content type (default: auto from category)")
    store_p.add_argument("--title", help="Human-readable title")
    store_p.add_argument("--subdir", help="Subdirectory within the branch")
    store_p.set_defaults(func=cmd_store)

    # search
    search_p = subparsers.add_parser("search", help="Search brain by tags and keywords")
    search_p.add_argument("--query", required=True, help="Search query")
    search_p.add_argument("--category", choices=CATEGORIES, help="Limit to one category")
    search_p.add_argument("--type", choices=CONTENT_TYPES, help="Filter by content type")
    search_p.add_argument("--limit", type=int, default=5, help="Max results")
    search_p.set_defaults(func=cmd_search)

    # list
    list_p = subparsers.add_parser("list", help="List all entries in the brain")
    list_p.add_argument("--category", choices=CATEGORIES, help="Limit to one category")
    list_p.add_argument("--type", choices=CONTENT_TYPES, help="Filter by content type")
    list_p.set_defaults(func=cmd_list)

    # read
    read_p = subparsers.add_parser("read", help="Read full content of a brain entry")
    read_p.add_argument("--category", required=True, choices=CATEGORIES)
    read_p.add_argument("--path", required=True, help="Path within the branch")
    read_p.set_defaults(func=cmd_read)

    # ingest
    ingest_p = subparsers.add_parser("ingest", help="Copy a file into a brain branch")
    ingest_p.add_argument("--file", required=True, help="File to ingest")
    ingest_p.add_argument("--category", default="patterns", choices=CATEGORIES)
    ingest_p.add_argument("--tags", help="Comma-separated tags")
    ingest_p.add_argument("--type", choices=CONTENT_TYPES, default="reference",
                          help="Content type (default: reference)")
    ingest_p.add_argument("--subdir", help="Subdirectory within the branch")
    ingest_p.set_defaults(func=cmd_ingest)

    # sync
    sync_p = subparsers.add_parser("sync", help="Smart sync brain branches to/from remote")
    sync_p.set_defaults(func=cmd_sync)

    # status
    status_p = subparsers.add_parser("status", help="Show brain branch status")
    status_p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
