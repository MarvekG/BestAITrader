#!/bin/sh
set -eu

python migration_bootstrap.py
exec "$@"
