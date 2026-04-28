"""Push snapshot files to a remote repo using the system `git` binary.

Auth is delegated entirely to the user's git configuration (SSH key,
credential helper, gh CLI, etc.) — no token is ever handled by the plugin.
"""

import os
import shutil
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def _run(args, cwd=None, env=None):
    try:
        proc = subprocess.run(
            args, cwd=cwd, env=env,
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as exc:
        raise GitError(f"git not found on PATH: {exc}") from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise GitError(f"{' '.join(args)}\n{msg}")
    return proc.stdout


def to_clone_url(spec):
    """Accept owner/repo, https://…, git@…, or a local filesystem path; return
    a value usable as a `git clone` source."""
    spec = spec.strip()
    if spec.startswith(("http://", "https://", "git@", "ssh://", "git://")):
        return spec
    if spec.startswith(("/", "./", "../", "~")):
        return os.path.expanduser(spec)
    parts = spec.split("/")
    if len(parts) == 2 and all(parts) and " " not in spec:
        return f"https://github.com/{spec}.git"
    raise ValueError(f"Cannot interpret repo spec: {spec!r}")


def _clone(remote_url, branch, dest):
    """Try to shallow-clone the requested branch. If it doesn't exist on the
    remote, clone the default branch and create `branch` locally so the first
    push will publish it. Returns True if branch already existed remotely."""
    dest = str(dest)
    try:
        _run(["git", "clone", "--depth", "1", "--branch", branch, remote_url, dest])
        return True
    except GitError:
        _run(["git", "clone", "--depth", "1", remote_url, dest])
        _run(["git", "checkout", "-b", branch], cwd=dest)
        return False


def _clear_datasets_dir(repo_dir, base_path):
    """Remove any tracked files under <base_path>/datasets so layers removed
    from the QGIS project also disappear from the repo. Other files in
    base_path are left alone."""
    rel = Path(base_path) / "datasets" if base_path else Path("datasets")
    datasets_dir = Path(repo_dir) / rel
    if datasets_dir.exists():
        shutil.rmtree(datasets_dir)


def push_snapshot(
    remote_url,
    branch,
    base_path,
    files,
    message,
    work_dir,
    author_name=None,
    author_email=None,
):
    """files: iterable of (repo_relative_path, abs_source_path).

    Clones the remote shallowly into a fresh dir under work_dir, replaces
    <base_path>/layers, copies all files into place, commits and pushes.
    Returns a result dict.
    """
    remote_url = to_clone_url(remote_url)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = work_dir / "repo"
    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    branch_existed = _clone(remote_url, branch, repo_dir)

    _clear_datasets_dir(repo_dir, base_path)

    written = []
    for rel_path, src in files:
        rel = Path(base_path) / rel_path if base_path else Path(rel_path)
        dst = repo_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        written.append(str(rel))

    _run(["git", "add", "-A"], cwd=repo_dir)

    # Anything to commit?
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=repo_dir
    )
    if diff.returncode == 0:
        return {"committed": False, "pushed": False, "files": written, "branch": branch}

    commit_args = ["git"]
    if author_name:
        commit_args += ["-c", f"user.name={author_name}"]
    if author_email:
        commit_args += ["-c", f"user.email={author_email}"]
    commit_args += ["commit", "-m", message]

    env = os.environ.copy()
    if author_name:
        env.setdefault("GIT_AUTHOR_NAME", author_name)
        env.setdefault("GIT_COMMITTER_NAME", author_name)
    if author_email:
        env.setdefault("GIT_AUTHOR_EMAIL", author_email)
        env.setdefault("GIT_COMMITTER_EMAIL", author_email)

    _run(commit_args, cwd=repo_dir, env=env)

    push_args = ["git", "push"]
    if not branch_existed:
        push_args += ["-u"]
    push_args += ["origin", branch]
    _run(push_args, cwd=repo_dir, env=env)

    return {"committed": True, "pushed": True, "files": written, "branch": branch}
