#!/bin/sh
# Install the virtual classroom platform on a Linux server.
#
# Usage:
#   sudo ./install.sh [--dir /opt/bacirat] [--user bacirat] [--port 8443]
#
# The script builds the server, creates the data directories and
# installs a systemd unit.  It is idempotent: running it again simply
# rebuilds and restarts the service.

set -eu

DIR=/opt/bacirat
USER=bacirat
PORT=8443

while [ $# -gt 0 ]; do
    case "$1" in
        --dir)  DIR=$2; shift 2 ;;
        --user) USER=$2; shift 2 ;;
        --port) PORT=$2; shift 2 ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

REPO=$(cd "$(dirname "$0")" && pwd)

command -v go >/dev/null 2>&1 || {
    echo "error: the Go toolchain is not installed." >&2
    echo "Install Go 1.24 or later, then run this script again." >&2
    exit 1
}

echo "==> Building the server"
cd "$REPO/galene"
CGO_ENABLED=0 go build -ldflags='-s -w'
cd "$REPO"

echo "==> Creating $USER"
if ! id "$USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$DIR" --shell /usr/sbin/nologin "$USER"
fi

echo "==> Installing into $DIR"
mkdir -p "$DIR/groups" "$DIR/data" "$DIR/recordings" "$DIR/static"
cp "$REPO/galene/galene" "$DIR/galene"
cp -r "$REPO/galene/static/." "$DIR/static/"

# galenectl is only needed to hash the administrator password, so it
# is built into a temporary location and not installed.
echo "==> Creating the administrator account"
if [ ! -f "$DIR/data/config.json" ]; then
    (cd "$REPO/galene/galenectl" && CGO_ENABLED=0 go build -o /tmp/galenectl .)

    PASSWORD=$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 20)
    PWJSON=$(/tmp/galenectl hash-password -password "$PASSWORD")
    HASH=$(printf '%s\n' "$PWJSON" \
           | sed -n 's/.*"key"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    rm -f /tmp/galenectl

    if [ -n "$HASH" ]; then
        cat > "$DIR/data/config.json" <<EOF
{
    "writableGroups": true,
    "users": {
        "admin": {
            "password": {
                "type": "bcrypt",
                "key": "$HASH"
            },
            "permissions": "admin"
        }
    }
}
EOF
        echo "    administrator login:    admin"
        echo "    administrator password: $PASSWORD"
        echo "    (this password is shown only once)"
    else
        cat > "$DIR/data/config.json" <<EOF
{
    "writableGroups": true,
    "users": {
        "admin": {
            "password": {
                "type": "plain",
                "key": "$PASSWORD"
            },
            "permissions": "admin"
        }
    }
}
EOF
        echo "    administrator login:    admin"
        echo "    administrator password: $PASSWORD"
        echo "    WARNING: could not hash the password, it is stored in plain text"
    fi
fi

chown -R "$USER:$USER" "$DIR"
chmod 700 "$DIR/data"
chmod 755 "$DIR/galene"

echo "==> Installing the systemd unit"
cat > "/etc/systemd/system/bacirat.service" <<EOF
[Unit]
Description=Virtual classroom server
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$DIR
ExecStart=$DIR/galene -http :$PORT -static $DIR/static/ \\
          -data $DIR/data/ -groups $DIR/groups/ \\
          -recordings $DIR/recordings/
Restart=on-failure
RestartSec=5
LimitNOFILE=65535

# Hardening: the server needs no privileges beyond its own files.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DIR/data $DIR/groups $DIR/recordings

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable bacirat.service
systemctl restart bacirat.service

echo "==> Done"
echo "Class page:  https://$(hostname):$PORT/group/NAME/"
echo "Admin panel: https://$(hostname):$PORT/admin.html"
