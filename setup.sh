#!/bin/sh
# wordle - Developed by acidvegas in Python (https://github.com/acidvegas)
# setup.sh

set -e
docker build -t wordle .
docker run --rm --user root -v wordle_data:/data wordle chown -R app:app /data # data made by older root containers
docker rm -f wordle 2>/dev/null || true
ARCADE_ARGS=''
if [ -n "$ARCADE_URL" ]; then
	ARCADE_ARGS="--network arcade -e ARCADE_URL -e ARCADE_PUBLIC_URL=${ARCADE_PUBLIC_URL:-https://arcade.{domain\}} -e ARCADE_GAME=wordle -e ARCADE_KEY"
fi
PUBLIC_ARGS=''
if [ -n "$WORDLE_PUBLIC_URL" ]; then
	PUBLIC_ARGS='-e WORDLE_PUBLIC_URL'
fi
docker run -d \
	--name wordle \
	--restart unless-stopped \
	-p ${WORDLE_BIND:-127.0.0.1}:20004:20004 \
	-e WORDLE_PROXIES=${WORDLE_PROXIES:-0} \
	-v wordle_data:/data \
	$ARCADE_ARGS \
	$PUBLIC_ARGS \
	wordle
