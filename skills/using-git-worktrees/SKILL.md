---
name: using-git-worktrees
description: Use when multiple agents or branches need isolated working directories in the same Git repository.
---

# Using Git Worktrees

Create one worktree per concurrent task so agents do not share uncommitted files or branches.

1. Reuse `.worktrees/`, then `worktrees/`; otherwise ask where to put worktrees.
2. For a repository-local directory, verify it is ignored with `git check-ignore`. Add it to `.gitignore` before proceeding if needed.
3. Create the branch and worktree with `git worktree add <path> -b <branch>`.
4. Run only the project's required setup, then its smallest relevant baseline check.
5. Report the worktree path, branch, and baseline result.

Do not create two worktrees for the same branch. Do not proceed after a failing baseline without reporting it.
