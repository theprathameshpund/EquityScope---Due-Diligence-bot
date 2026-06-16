#!/usr/bin/env bash
curl -sf -X DELETE http://localhost:6333/collections/equityscope_filings
echo ""
curl -s http://localhost:6333/collections | python3 -c "import sys,json; d=json.load(sys.stdin); print('remaining:', [c['name'] for c in d['result']['collections']])"
