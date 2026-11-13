#!/usr/bin/env bash
# Checks a built image: it must not run as root, and it must have a health
# check whose command succeeds. A Dockerfile can say USER and still ship an
# image that runs as root if a later layer changes it back, so this asks the
# built image rather than reading the source.
set -euo pipefail
image="$1"
echo "inspecting $image"

uid="$(docker run --rm --entrypoint id "$image" -u)"
if [ "$uid" = "0" ]; then
  echo "::error::the image runs as root (uid 0)"
  exit 1
fi
echo "runs as uid $uid"

health="$(docker inspect --format '{{json .Config.Healthcheck}}' "$image")"
if [ "$health" = "null" ]; then
  echo "::error::the image has no HEALTHCHECK"
  exit 1
fi
echo "healthcheck: $health"

# And the health command has to succeed, or the container is permanently
# unhealthy. pywrkr has no --version, which is the obvious thing to reach for.
docker run --rm "$image" --help > /dev/null

# A relative report path has to work without -w: the working directory must be
# writable by the runtime user. The target is a closed port, so the run records
# connection errors, but it still writes its report and exits 0.
if ! docker run --rm --entrypoint sh "$image" -c \
    'pywrkr http://127.0.0.1:1/ -n 1 --json results.json > /dev/null && test -s results.json'; then
  echo "::error::the image cannot write a relative --json report in its working directory ($(docker inspect --format '{{.Config.WorkingDir}}' "$image"))"
  exit 1
fi
echo "writes a relative report in $(docker inspect --format '{{.Config.WorkingDir}}' "$image")"
