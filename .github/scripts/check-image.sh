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
