"""Single-backend SQLite store; initialization and recovery run under the CLI lock."""

import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from .models import DeviceConfig, MusicReport, Settings, device_adapter
from .security import HASHER, digest, random_password, token


class Store:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "sleepy.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")

    def close(self):
        self.db.close()

    def initialize(self) -> str | None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS admin (
                id INTEGER PRIMARY KEY CHECK(id=1), password TEXT NOT NULL,
                must_change INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE,
                config TEXT NOT NULL, state TEXT NOT NULL, last_updated REAL
            );
            CREATE TABLE IF NOT EXISTS smtc (
                id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
                key_hash TEXT NOT NULL, track TEXT, last_updated REAL
            );
            CREATE TABLE IF NOT EXISTS settings_tokens (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL,
                authenticated INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS manual_status (
                id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL, expires REAL
            );
            CREATE TABLE IF NOT EXISTS metrics (
                minute INTEGER NOT NULL, category TEXT NOT NULL, route TEXT NOT NULL,
                method TEXT NOT NULL, status INTEGER NOT NULL, count INTEGER NOT NULL,
                PRIMARY KEY (minute, category, route, method, status)
            );
            CREATE TABLE IF NOT EXISTS metric_batches (
                run TEXT PRIMARY KEY, sequence INTEGER NOT NULL
            );
        """)
        password = None
        with self.db:
            if not self.db.execute("SELECT 1 FROM admin").fetchone():
                password = random_password()
                self.db.execute("INSERT INTO admin VALUES (1, ?, 1)", (HASHER.hash(password),))
            self.db.execute(
                "INSERT OR IGNORE INTO settings VALUES (1, ?)", (Settings().model_dump_json(),)
            )
            # Clear the retired stock description on pre-display-settings installations.
            self.db.execute(
                """UPDATE settings SET data=json_set(data, '$.description', '', '$.display', json('{}'))
                   WHERE json_type(data, '$.display') IS NULL
                     AND json_extract(data, '$.description')=?""",
                ("一些设备，一点日常。",),
            )
            self.db.execute(
                """UPDATE settings SET data=json_remove(data, '$.display.in_use',
                          '$.display.not_using', '$.display.device_slice')
                   WHERE json_type(data, '$.display.in_use') IS NOT NULL
                      OR json_type(data, '$.display.not_using') IS NOT NULL
                      OR json_type(data, '$.display.device_slice') IS NOT NULL"""
            )
        self.path.chmod(0o600)
        return password

    def admin(self):
        return self.db.execute("SELECT * FROM admin WHERE id=1").fetchone()

    def change_password(self, encoded: str, must_change=False):
        with self.db:
            self.db.execute(
                "UPDATE admin SET password=?, must_change=? WHERE id=1", (encoded, must_change)
            )
            self.db.execute("DELETE FROM sessions")

    def settings(self) -> Settings:
        return Settings.model_validate_json(
            self.db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0]
        )

    def save_settings(self, settings: Settings):
        with self.db:
            self.db.execute("UPDATE settings SET data=? WHERE id=1", (settings.model_dump_json(),))

    def settings_tokens(self):
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT id, name, created_at FROM settings_tokens ORDER BY created_at DESC"
            )
        ]

    def create_settings_token(self, name: str):
        info = {"id": uuid4().hex, "name": name, "created_at": time.time()}
        value = "sly_" + token()
        with self.db:
            self.db.execute(
                "INSERT INTO settings_tokens(id, name, token_hash, created_at) VALUES (?, ?, ?, ?)",
                (info["id"], name, digest(value), info["created_at"]),
            )
        return {**info, "token": value}

    def valid_settings_token(self, value: str):
        return (
            self.db.execute(
                "SELECT 1 FROM settings_tokens WHERE token_hash=?", (digest(value),)
            ).fetchone()
            is not None
        )

    def revoke_settings_token(self, token_id: str):
        with self.db:
            return (
                self.db.execute("DELETE FROM settings_tokens WHERE id=?", (token_id,)).rowcount > 0
            )

    def new_session(self, authenticated: bool, lifetime: int):
        value, csrf, expires = token(), token(), time.time() + lifetime
        with self.db:
            self.db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
            # Pre-login challenges are bounded; they must not evict authenticated sessions.
            self.db.execute(
                "DELETE FROM sessions WHERE authenticated=0 AND token_hash NOT IN "
                "(SELECT token_hash FROM sessions WHERE authenticated=0 ORDER BY expires DESC LIMIT 1000)"
            )
            self.db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                (digest(value), csrf, expires, authenticated),
            )
        return value, csrf, expires

    def session(self, value: str | None):
        if not value:
            return None
        row = self.db.execute(
            "SELECT * FROM sessions WHERE token_hash=? AND expires>?", (digest(value), time.time())
        ).fetchone()
        return dict(row) if row else None

    def delete_session(self, value: str):
        with self.db:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(value),))

    @staticmethod
    def decode_device(row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "config": DeviceConfig.model_validate_json(row["config"]),
            "state": json.loads(row["state"]),
            "last_updated": row["last_updated"],
        }

    def devices(self):
        return [self.decode_device(row) for row in self.db.execute("SELECT * FROM devices")]

    def device(self, id: str):
        return self.decode_device(
            self.db.execute("SELECT * FROM devices WHERE id=?", (id,)).fetchone()
        )

    def device_by_key(self, key: str):
        return self.decode_device(
            self.db.execute("SELECT * FROM devices WHERE key_hash=?", (digest(key),)).fetchone()
        )

    def create_device(self, config: DeviceConfig):
        id, key = uuid4().hex, token()
        state = device_adapter(config).validate({})
        with self.db:
            self.db.execute(
                "INSERT INTO devices VALUES (?, ?, ?, ?, NULL)",
                (id, digest(key), config.model_dump_json(), json.dumps(state)),
            )
        return id, key

    def update_device(self, id: str, config: DeviceConfig):
        previous = self.device(id)
        if previous is None:
            raise KeyError(id)
        changed_type = (previous["config"].kind, previous["config"].os) != (config.kind, config.os)
        previous_types = {f.key: f.type for f in previous["config"].custom_fields}
        values = {} if changed_type else previous["state"].copy()
        for field in config.custom_fields:
            if previous_types.get(field.key) != field.type:
                values.pop(field.key, None)
        state = device_adapter(config).validate(values)
        with self.db:
            self.db.execute(
                "UPDATE devices SET config=?, state=?, last_updated=? WHERE id=?",
                (
                    config.model_dump_json(),
                    json.dumps(state),
                    None if changed_type else previous["last_updated"],
                    id,
                ),
            )

    def rotate_key(self, id: str):
        key = token()
        with self.db:
            self.db.execute("UPDATE devices SET key_hash=? WHERE id=?", (digest(key), id))
        return key

    def delete_device(self, id: str):
        with self.db:
            self.db.execute("DELETE FROM devices WHERE id=?", (id,))

    def report(self, id: str, state: dict):
        with self.db:
            self.db.execute(
                "UPDATE devices SET state=?, last_updated=? WHERE id=?",
                (json.dumps(state), time.time(), id),
            )

    def set_smtc(self, enabled: bool):
        with self.db:
            if enabled:
                key = token()
                inserted = self.db.execute(
                    "INSERT OR IGNORE INTO smtc(id, enabled, key_hash) VALUES (1,1,?)",
                    (digest(key),),
                )
                if inserted.rowcount:
                    return key
                self.db.execute("UPDATE smtc SET enabled=1 WHERE id=1")
            else:
                self.db.execute(
                    "UPDATE smtc SET enabled=0, track=NULL, last_updated=NULL WHERE id=1"
                )
        return None

    def rotate_smtc_key(self):
        key = token()
        with self.db:
            result = self.db.execute(
                "UPDATE smtc SET key_hash=? WHERE id=1 AND enabled=1", (digest(key),)
            )
        return key if result.rowcount else None

    def report_smtc(self, key: str, report: MusicReport):
        with self.db:
            result = self.db.execute(
                "UPDATE smtc SET track=?, last_updated=? WHERE id=1 AND enabled=1 AND key_hash=?",
                (report.model_dump_json(), time.time(), digest(key)),
            )
        return bool(result.rowcount)

    def smtc(self, now: float):
        row = self.db.execute(
            "SELECT track, last_updated FROM smtc WHERE id=1 AND enabled=1"
        ).fetchone()
        if row is None:
            return None
        if row["last_updated"] is None or now - row["last_updated"] >= 90:
            return {"playback": "unknown"}
        return json.loads(row["track"])

    def manual(self):
        row = self.db.execute("SELECT * FROM manual_status WHERE id=1").fetchone()
        return dict(row) if row else None

    def set_manual(self, status: str | None, duration: int | None):
        with self.db:
            self.db.execute("DELETE FROM manual_status")
            if status:
                self.db.execute(
                    "INSERT INTO manual_status VALUES (1, ?, ?)",
                    (status, time.time() + duration if duration is not None else None),
                )

    def ingest_metrics(self, run: str, sequence: int, rows: list[dict]):
        with self.db:
            previous = self.db.execute(
                "SELECT sequence FROM metric_batches WHERE run=?", (run,)
            ).fetchone()
            if previous and previous[0] >= sequence:
                return
            for row in rows:
                self.db.execute(
                    """INSERT INTO metrics VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(minute, category, route, method, status)
                    DO UPDATE SET count=count+excluded.count""",
                    (
                        row["minute"],
                        row["category"],
                        row["route"],
                        row["method"],
                        row["status"],
                        row["count"],
                    ),
                )
            self.db.execute(
                "INSERT INTO metric_batches VALUES (?, ?) ON CONFLICT(run) DO UPDATE SET sequence=excluded.sequence",
                (run, sequence),
            )
