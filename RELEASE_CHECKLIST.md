# Release Checklist

Use this checklist before merging a production release into `main`.

## 1. Working tree

- [ ] Current functionality is manually smoke-tested where browser/provider credentials are available.
- [ ] No `.env` or real credentials are present in the working tree.
- [ ] No `db.sqlite3` or other local database is committed.
- [ ] No runtime `media/` files are committed.
- [ ] No `staticfiles/`, `__pycache__/`, `.venv/`, logs or editor artifacts are committed.
- [ ] `pg_test_settings.py` has been removed; it is verification-only and is explicitly marked non-shipping in the current source.

## 2. Source verification

```bash
python -m pip install -r requirements.txt
pip check
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test accounts
python manage.py findstatic css/tokens.css
python manage.py findstatic css/components.css
python manage.py findstatic css/shell.css
python manage.py collectstatic --noinput
```

Record actual output. Do not copy a historical test count into a release note without rerunning the suite.

## 3. Git safety

```bash
git status
git diff --check
git diff --stat
git remote -v
git log --oneline --decorate --graph -15
```

Confirm the remote is the existing repository:

```text
https://github.com/vivektyagi05/Ai_Generator
```

## 4. Release branch

```bash
git switch main
git pull --ff-only origin main
git switch -c release/industry-hardening-2026
```

Do not delete `.git`, re-run `git init`, force-push, or rewrite existing history.

## 5. Stage and inspect

```bash
git add -A
git status

git diff --cached --check
git diff --cached --stat
git diff --cached
```

Pay particular attention to `.env`, API keys, payment secrets, local databases, uploaded media and generated files.

## 6. Commit

```bash
git commit -m "chore: prepare production repository release"
```

## 7. Push branch

```bash
git push -u origin release/industry-hardening-2026
```

## 8. Pull request

Open a PR from `release/industry-hardening-2026` into `main`.

Require the CI workflow to pass. Review the actual diff, not only the PR summary.

## 9. Merge

After CI/review:

```bash
git switch main
git pull --ff-only origin main
```

The merge strategy should preserve existing repository history.

## 10. Tag the release

After the merge is confirmed:

```bash
git tag -a v1.0.0 -m "AI Generator v1.0.0"
git push origin v1.0.0
```

Only use `v1.0.0` if this is genuinely the first public release under your chosen versioning policy. Otherwise choose the correct next semantic version.
