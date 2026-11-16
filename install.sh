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

# 'sudo' ignores /etc/profile.d, so a Go installed under /usr/local/go
# is not on sudo's PATH.  Look for it there before giving up.
if ! command -v go >/dev/null 2>&1; then
    for d in /usr/local/go/bin /usr/lib/go/bin /usr/lib/go-*/bin \
             "$HOME/go/bin" /usr/local/bin; do
        if [ -x "$d/go" ]; then
            PATH="$PATH:$d"
            export PATH
            break
        fi
    done
fi

if ! command -v go >/dev/null 2>&1; then
    echo "error: the Go toolchain is not installed." >&2
    echo "Install Go 1.24 or later, then run this script again." >&2
    echo "See PREPARE.md, section 5." >&2
    exit 1
fi

# go.mod requires go >= 1.24.  Compare major and minor separately:
# Go 1.9 is older than 1.24 even though 1.9 is the larger number.
GOV=$(go env GOVERSION 2>/dev/null | sed 's/^go//')
[ -n "$GOV" ] || GOV=$(go version | sed 's/.*go\([0-9]*\.[0-9]*\).*/\1/')
GOMAJ=$(printf '%s\n' "$GOV" | cut -d. -f1)
GOMIN=$(printf '%s\n' "$GOV" | cut -d. -f2)
case "$GOMAJ$GOMIN" in
    *[!0-9]*)
        echo "    warning: cannot parse the Go version '$GOV', continuing" ;;
    *)
        if ! { [ "$GOMAJ" -gt 1 ] || { [ "$GOMAJ" -eq 1 ] &&
                                      [ "$GOMIN" -ge 24 ]; }; }; then
            echo "error: Go $GOV is too old, 1.24 or later is required." >&2
            echo "Install a newer Go (PREPARE.md, section 5), then run again." >&2
            exit 1
        fi ;;
esac
echo "    using go $GOV"

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

# The school exporter reads the management database read-only; it is
# only installed, never enabled, so that an installation without a
# school database keeps working.
if [ -f "$REPO/export-school.php" ]; then
    cp "$REPO/export-school.php" "$DIR/export-school.php"
    chmod 700 "$DIR/export-school.php"
    echo "    school exporter installed at $DIR/export-school.php (not enabled)"
fi
if [ -f "$REPO/school.json.example" ]; then
    cp "$REPO/school.json.example" "$DIR/data/school.json.example"
fi

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
