#!/bin/bash
# Example showing how to purge entries
now=$(date +"%m_%d_%Y")
fn=purged_$now.csv
echo "Purging suppression list entries into $fn"
pipenv run ./sparkySuppress.py purge $fn
