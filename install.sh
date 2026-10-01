#!/usr/bin/env bash
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP=/home/copycatbrain/universal-nodes

mkdir -p "$APP/captures"

cp "$SRC/index.html" "$APP/index.html"
cp "$SRC/studio.js" "$APP/studio.js"
cp "$SRC/studio.css" "$APP/studio.css"
cp "$SRC/server.py" "$APP/server.py"
chown -R copycatbrain:copycatbrain "$APP"

cat >/etc/systemd/system/bookscanner-universal-nodes.service <<'EOF'
[Unit]
Description=Book Scanner Universal Node Controller V6
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=copycatbrain
WorkingDirectory=/home/copycatbrain/universal-nodes
ExecStart=/usr/bin/python3 /home/copycatbrain/universal-nodes/server.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable bookscanner-universal-nodes.service
systemctl restart bookscanner-universal-nodes.service

echo
echo "Book Scanner V6 installed."
echo "Open this on the Lenovo:"
echo "http://10.42.0.2:8093"
echo
echo "No JavaScript/CSS CDN is used."
echo "The page, nodes, Pi Terminal, and Pi Files editor are self-contained."
