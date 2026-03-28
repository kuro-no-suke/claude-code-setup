#!/bin/bash
# このスクリプトがあるディレクトリに移動してClaude Codeを起動する
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
claude
