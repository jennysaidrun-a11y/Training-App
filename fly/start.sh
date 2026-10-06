#!/usr/bin/env bash
# Starts the training app on a Fly.io machine.
# /data is the machine's own disk: the app's copy of the repo (/data/repo) and the
# employee records (/data/repo/data, never sent to GitHub) live there.
set -u
REPO_URL=${REPO_URL:-https://github.com/jennysaidrun-a11y/Training-App.git}
BRANCH=${BRANCH:-claude/project-thread-f2cmko}

git config --global user.name "Training app (online)"
git config --global user.email "training-app@users.noreply.github.com"
git config --global --add safe.directory /data/repo
# Saving lesson edits to GitHub uses the GITHUB_TOKEN Fly secret, read when needed
# so it is never written to disk.
if [ -n "${GITHUB_TOKEN:-}" ]; then
  git config --global credential.helper '!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'
fi

if [ ! -d /data/repo/.git ]; then
  git clone -q -b "$BRANCH" "$REPO_URL" /data/repo.tmp && mv /data/repo.tmp /data/repo
fi
cd /data/repo
export PORT=${PORT:-8080} HOST=0.0.0.0 SAVE_LABEL="Online" SKIP_CLAUDE=1
exec bash update.sh
