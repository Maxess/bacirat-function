#!/usr/bin/env python3
"""Prepare a private, one-room PersianMeet v0.26.3 pilot on Ubuntu/systemd."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener
from zipfile import ZipFile


ARCHIVE_SHA256 = "ff417f6dafde8bed6c8ceccd4422f6e92a6c14364cfab7ac7b06e201508ec1da"
NAME = "persianmeet-pilot"
INSTALL = Path("/var/lib") / NAME
UNIT = Path("/etc/systemd/system") / (NAME + ".service")
ROOM_SIZE = 4
TITLE = "کلاس آزمایشی"
BASE_URL = "http://127.0.0.1:8003"
OPENER = build_opener(ProxyHandler({}))

SERVICE = f"""[Unit]
Description=PersianMeet classroom pilot (private by default)
After=network.target

[Service]
Type=simple
User={NAME}
Group={NAME}
WorkingDirectory={INSTALL}
ExecStart={INSTALL}/persianmeet
Environment=HOME={INSTALL}/data GOMAXPROCS=1
UMask=0077
Restart=on-failure
RestartSec=4
PrivateNetwork=yes
PrivateTmp=yes
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths={INSTALL}/data

[Install]
WantedBy=multi-user.target
"""


class PilotError(Exception):
    pass


def replace_keys(path, values):
    """Edit the known config layout in the pinned archive; fail on drift."""
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        pattern = rf"(?m)^{re.escape(key)}[ \t]*:[^\n]*$"
        text, count = re.subn(pattern, lambda _: f"{key}: {value}", text)
        if count != 1:
            raise PilotError(f"Unexpected config layout: {path.name}, {key.strip()}")
    path.write_text(text, encoding="utf-8")


def prepare(archive, work, uid, gid):
    # All extraction is from our private, verified copy, not the caller's path.
    bundle = work / "bundle.zip"
    with archive.open("rb") as source, bundle.open("xb") as target:
        bundle.chmod(0o600)
        shutil.copyfileobj(source, target)
    with bundle.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != ARCHIVE_SHA256:
            raise PilotError("Archive does not match the repository's v0.26.3 ZIP")
    with ZipFile(bundle) as package:
        for item in package.infolist():
            parts = item.filename.rstrip("/").split("/")
            if parts[0] != "persianmeet" or any(p in ("", ".", "..") or "\\" in p for p in parts):
                raise PilotError("Unexpected path in the release ZIP")
            if ".idea" not in parts:
                package.extract(item, work)
    bundle.unlink()

    stage = work / "persianmeet"
    os.chown(stage, 0, gid)
    stage.chmod(0o750)
    data = stage / "data"
    data.mkdir(mode=0o700)
    os.chown(data, uid, gid)
    for directory in (data / "upload", data / "up"):
        directory.mkdir(mode=0o700)
        os.chown(directory, uid, gid)
    # The binary initializes ./up even with Office integration disabled.
    (stage / "up").symlink_to("data/up", target_is_directory=True)
    for file in (stage / "persianmeet", stage / "app/lit"):
        file.chmod(0o755)
    cfg = stage / "config.yml"
    replace_keys(cfg, {
        "api_secret": json.dumps(secrets.token_hex(32)),
        "demo_meet_id": "0",
        "max_user_count_in_room": str(ROOM_SIZE),
        "max_video_count_in_room": "0",
        "connection_string": '"./data/pilot.db"',
        "  path": '"./data/upload"',
        "  apikey": json.dumps(secrets.token_hex(16)),
        "  secret": json.dumps(secrets.token_hex(32)),
        "  internal_turn": "false",
        "  optimized": "false",
    })
    os.chown(cfg, 0, gid)
    cfg.chmod(0o640)
    replace_keys(stage / "dist/config.js", {
        "  lang": "'fa',",
        "  siteName": "'کلاس آزمایشی',",
        "  maxUserCountInRoom": f"{ROOM_SIZE},",
        "  demo_meet_id": "0,",
    })
    # The service user may traverse the temporary parent to run the seed app.
    work.chmod(0o711)
    return stage


def api(operation, data, key=""):
    request = Request(
        BASE_URL + "/api",
        data=json.dumps({"Type": operation, "Secret": key, "Data": data}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with OPENER.open(request, timeout=4) as response:
            answer = json.loads(response.read(65536))
    except (OSError, ValueError) as error:
        raise PilotError(f"Local API call failed: {operation}") from error
    if not isinstance(answer, dict):
        raise PilotError(f"Unexpected local API response: {operation}")
    return answer


def require_ok(result, operation):
    if result.get("Status") != "ok":
        raise PilotError(f"Application did not accept {operation}")
    return result


def wait_for_app(process):
    for _ in range(48):
        if process.poll() is not None:
            raise PilotError("Application stopped during private startup")
        try:
            with OPENER.open(BASE_URL + "/", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(0.125)
    raise PilotError("Application did not become ready in the private namespace")


def configure_room(stage):
    db_path = stage / "data/pilot.db"
    # Read the seed credential only on this fresh installation, never from a live DB.
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as db:
        admin_rows = db.execute("SELECT UserName, Password FROM PanelUser WHERE Role=1").fetchall()
        meet_ids = db.execute("SELECT ID FROM MeetInfo").fetchall()
    if len(admin_rows) != 1 or meet_ids != [(1,)]:
        raise PilotError("Expected one seed administrator and one seed room")
    username, seed_password = admin_rows[0]
    admin_password = secrets.token_urlsafe(24)
    teacher_password = secrets.token_urlsafe(18)
    student_password = secrets.token_urlsafe(18)
    seed_login = {"UserName": username, "Password": seed_password}
    seed = require_ok(api("Login", seed_login), "initial login")
    require_ok(api("ChangePassword", {"Password": admin_password}, seed["GUID"]), "password change")
    if api("Login", seed_login).get("Status") != "passNotMatch":
        raise PilotError("The seed password was not rejected after rotation")
    admin = require_ok(api("Login", {"UserName": username, "Password": admin_password}), "new login")
    if admin["GUID"] == seed["GUID"]:
        raise PilotError("The seed session was not rotated")
    require_ok(api("EditMeet", {
        "ID": 1, "Title": TITLE, "OwnerID": admin["ID"], "UserCount": ROOM_SIZE,
        "UserNeedPass": True, "AdminNeedPass": True,
        "UserPass": student_password, "AdminPass": teacher_password,
    }, admin["GUID"]), "pilot room configuration")
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as db:
        rooms = db.execute("SELECT ID, Title, UserCount, UserNeedPass, AdminNeedPass FROM MeetInfo").fetchall()
    if rooms != [(1, TITLE, ROOM_SIZE, 1, 1)]:
        raise PilotError("Private room configuration was not persisted")
    access = stage / "pilot-access.txt"
    fd = os.open(access, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(f"Panel: /dash/login\nPanel username: {username}\nPanel password: {admin_password}\n"
                   f"Pilot room: /room/1\nTeacher password: {teacher_password}\nStudent password: {student_password}\n")


def bootstrap(stage, uid, gid):
    stage = stage.resolve(strict=True)
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise PilotError("Refusing to bootstrap outside a loopback-only network namespace")
    if (stage / "data/pilot.db").exists() or (stage / "pilot-access.txt").exists():
        raise PilotError("Bootstrap requires an unused pilot directory")
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    options = {} if uid is None else {"user": uid, "group": gid, "extra_groups": []}
    # The vendor's startup log contains its SFU key; don't copy it into reports.
    process = subprocess.Popen([str(stage / "persianmeet")], cwd=stage,
                               env={"PATH": "/usr/bin:/bin", "HOME": str(stage / "data"), "GOMAXPROCS": "1"},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True, umask=0o077, **options)
    try:
        wait_for_app(process)
        try:
            OPENER.open(BASE_URL + "/sfu/rtc/validate", timeout=2).close()
        except HTTPError as error:
            if error.code != 401:
                raise PilotError("Unexpected status from the SFU signaling endpoint") from error
            error.close()
        else:
            raise PilotError("The SFU signaling route did not require authentication")
        configure_room(stage)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def install(archive):
    if os.geteuid() != 0 or platform.system() != "Linux" or platform.machine() != "x86_64":
        raise PilotError("Run with sudo on Linux x86_64")
    if not Path("/run/systemd/system").is_dir():
        raise PilotError("This installer requires systemd as the service manager")
    if any(os.path.lexists(path) for path in (INSTALL, UNIT)):
        raise PilotError("Pilot files already exist; this installer will not overwrite them")
    if not archive.is_file():
        raise PilotError("The release ZIP was not found")

    os.umask(0o022)
    with tempfile.TemporaryDirectory(prefix=".persianmeet-pilot-", dir=INSTALL.parent) as scratch:
        try:
            account = pwd.getpwnam(NAME)
        except KeyError:
            # Check the archive first so a wrong file doesn't create a system user.
            with archive.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != ARCHIVE_SHA256:
                    raise PilotError("Archive does not match the repository's v0.26.3 ZIP")
            subprocess.run(["useradd", "--system", "--user-group", "--no-create-home", "--home-dir",
                            str(INSTALL / "data"), "--shell", "/usr/sbin/nologin", NAME], check=True)
            account = pwd.getpwnam(NAME)
        if (account.pw_uid == 0 or account.pw_gid == 0 or account.pw_dir != str(INSTALL / "data")
                or account.pw_shell != "/usr/sbin/nologin"):
            raise PilotError("An unsuitable account with the pilot service name already exists")

        stage = prepare(archive, Path(scratch), account.pw_uid, account.pw_gid)
        script = Path(__file__).resolve()
        subprocess.run(["unshare", "--net", "--", sys.executable, "-I", str(script),
                        "_seed", str(stage), str(account.pw_uid), str(account.pw_gid)], check=True)
        stage.rename(INSTALL)
        try:
            with UNIT.open("x", encoding="utf-8") as file:
                file.write(SERVICE)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise PilotError(f"Files prepared in {INSTALL}, but unit setup failed; inspect before retrying") from error

    print("pilot_ready=yes room=/room/1 room_limit=4 private_network=yes auto_start=no")
    print(f"Credentials (keep private): sudo cat {INSTALL}/pilot-access.txt")
    print(f"Start the private service: sudo systemctl start {NAME}")


def main():
    try:
        if len(sys.argv) == 5 and sys.argv[1] == "_seed":
            uid, gid = map(int, sys.argv[3:])
            if os.geteuid() != 0 or min(uid, gid) <= 0:
                raise PilotError("Private seeding requires a non-root service account")
            bootstrap(Path(sys.argv[2]), uid, gid)
        else:
            parser = argparse.ArgumentParser(description=__doc__)
            parser.add_argument("archive", type=Path, help="repository ZIP: persianmeet-linux-v0.26.3.zip")
            install(parser.parse_args().archive)
    except PilotError as error:
        sys.exit(f"Pilot setup: {error}")
    except subprocess.CalledProcessError as error:
        sys.exit(f"Pilot setup: {Path(error.cmd[0]).name} failed (exit {error.returncode})")
    except (KeyError, OSError, sqlite3.Error, ValueError) as error:
        sys.exit(f"Pilot setup failed ({type(error).__name__}); no credentials were printed")


if __name__ == "__main__":
    main()
