#!/bin/bash

cd "$(dirname "$0")"

echo "Pulling latest changes from GitHub..."
git pull origin main

echo ""
echo "Done! Your files are up to date."
