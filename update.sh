#!/usr/bin/env bash
# Runs the training app and keeps it up to date by itself.
# Every minute it:
#   1. saves lesson edits made on the Manager page (content/) to GitHub,
#   2. picks up any new version of the app from GitHub and restarts it.
# Employee records (data/) never leave this Codespace.
cd "$(dirname "$0")"
# Only one updater at a time (the Codespace starts it on every start).
exec 9>/tmp/training-update.lock
flock -n 9 || { echo "Updater already running."; exit 0; }
BRANCH=$(git rev-parse --abbrev-ref HEAD)
PORT=${PORT:-8000}

req_hash() { sha1sum requirements.txt 2>/dev/null | cut -c1-40; }
script_hash() { sha1sum update.sh | cut -c1-40; }

start_app() {
  pkill -f "python -m trainer" 2>/dev/null
  sleep 1
  nohup python -m trainer > app.log 2>&1 &
  echo "Training app running on port $PORT ($(git log --oneline -1))"
}

save_work() {
  if [ -n "$(git status --porcelain content)" ]; then
    git add -A content
    git commit -q -m "Codespace: lesson edits saved" && echo "Saved lesson edits."
  fi
}

sync() {
  save_work
  git fetch -q origin "$BRANCH" || return
  if [ "$(git rev-parse HEAD)" != "$(git rev-parse "origin/$BRANCH")" ]; then
    local before_req before_script
    before_req=$(req_hash); before_script=$(script_hash)
    # Your edits win if the same lesson changed on both sides.
    if git merge -q --no-edit -X ours "origin/$BRANCH"; then
      [ "$(req_hash)" != "$before_req" ] && pip install -q -r requirements.txt
      if [ "$(script_hash)" != "$before_script" ]; then
        echo "Updater changed; restarting it."
        exec bash update.sh
      fi
      start_app
    else
      git merge --abort 2>/dev/null
      echo "Couldn't merge the update. Send app.log and this message to Claude."
    fi
  fi
  # Send saved edits to GitHub.
  if [ -n "$(git log --oneline "origin/$BRANCH..HEAD" 2>/dev/null)" ]; then
    git push -q origin "HEAD:$BRANCH" || echo "Couldn't save to GitHub yet; will try again in a minute."
  fi
}

pip install -q -r requirements.txt
sync
start_app
while true; do
  sleep 60
  sync
  # Restart the app if it stopped for any reason.
  pgrep -f "python -m trainer" > /dev/null || start_app
done
