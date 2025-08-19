# db.py
from typing import Optional, Dict, Any, List, Tuple
import os, json, sqlite3
from datetime import datetime
from threading import Lock

# db.py (acrescente estes imports no topo)
import os, hmac, binascii
from typing import Optional, Dict, Any, List, Tuple

# --- util de hash de senha (PBKDF2-HMAC-SHA256) ---
_PBKDF2_ITER = 130_000

def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = _pbkdf2(password, salt)
    return f"pbkdf2${_PBKDF2_ITER}${binascii.hexlify(salt).decode()}${binascii.hexlify(dk).decode()}"

def _pbkdf2(password: str, salt: bytes) -> bytes:
    from hashlib import pbkdf2_hmac
    return pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITER, dklen=32)

def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        iters = int(iters)
        salt = binascii.unhexlify(salt_hex)
        expected = binascii.unhexlify(hash_hex)
        from hashlib import pbkdf2_hmac
        dk = pbkdf2_hmac("sha256", password.encode(), salt, iters, dklen=32)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False

DB_PATH = os.getenv("DB_PATH", "pp_platform.db")
_DB_LOCK = Lock()

def _conn():
    cn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cn.execute("PRAGMA journal_mode=WAL;")
    cn.execute("PRAGMA synchronous=NORMAL;")
    cn.execute("PRAGMA foreign_keys=ON;")
    return cn

def init_db():
    with _conn() as cn:
        cn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT,
            created_at TEXT
        )""")
        cn.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            profile_json TEXT,
            version INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )""")
        cn.execute("""
        CREATE TABLE IF NOT EXISTS eligibility_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            profile_id INTEGER,
            desired_policy TEXT,
            matched_policies_json TEXT,
            gaps_json TEXT,
            created_at TEXT
        )""")

def migrate_accounts():
    """Cria tabela de contas e vincula perfis a um dono (owner_account_id)."""
    with _conn() as cn:
        # Tabela de contas (pessoa/collectivo) com login/senha
        cn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT CHECK(kind IN ('person','collective')) NOT NULL,
            username TEXT UNIQUE,        -- para pessoa física (login)
            display_name TEXT,
            cnpj TEXT UNIQUE,            -- para coletivo (login)
            contact TEXT,                -- para coletivo
            password_hash TEXT NOT NULL,
            created_at TEXT
        )
        """)
        # Adiciona coluna owner_account_id em profiles, se não existir
        cols = [r[1] for r in cn.execute("PRAGMA table_info(profiles)").fetchall()]
        if "owner_account_id" not in cols:
            cn.execute("ALTER TABLE profiles ADD COLUMN owner_account_id INTEGER;")
import json as _json
from datetime import datetime

# --- ANALYTICS / OBSERVATÓRIO -----------------------------------
import json as _json
from datetime import datetime

def migrate_analytics():
    with _conn() as cn:
        cn.execute("""
        CREATE TABLE IF NOT EXISTS analytics_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,               -- 'search','view','matches','eligible'
            policy TEXT,
            uf TEXT,
            municipio TEXT,
            query TEXT,
            gender TEXT,                      -- NEW
            met_json TEXT,                    -- NEW (requisitos presentes/atendidos)
            missing_json TEXT,                -- requisitos faltantes
            extras_json TEXT
        )
        """)
        # Backfill de colunas novas (não dá erro se já existirem)
        try: cn.execute("ALTER TABLE analytics_events ADD COLUMN gender TEXT")
        except Exception: pass
        try: cn.execute("ALTER TABLE analytics_events ADD COLUMN met_json TEXT")
        except Exception: pass

def _now_iso():
    return datetime.utcnow().isoformat()

def log_event(kind: str,
              policy: str|None=None,
              uf: str|None=None,
              municipio: str|None=None,
              query: str|None=None,
              gender: str|None=None,
              met: list|None=None,
              missing: list|None=None,
              extras: dict|None=None):
    mj  = _json.dumps(met, ensure_ascii=False)     if met     is not None else None
    msj = _json.dumps(missing, ensure_ascii=False) if missing is not None else None
    ej  = _json.dumps(extras, ensure_ascii=False)  if extras  is not None else None
    with _DB_LOCK:
        with _conn() as cn:
            cn.execute("""
            INSERT INTO analytics_events (ts, kind, policy, uf, municipio, query, gender, met_json, missing_json, extras_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (_now_iso(), kind, policy, uf, municipio, query, gender, mj, msj, ej))

def get_analytics(start_iso: str|None=None, end_iso: str|None=None,
                  uf: str|None=None, municipio: str|None=None, gender: str|None=None):
    sql = """SELECT ts, kind, policy, uf, municipio, query, gender, met_json, missing_json, extras_json
             FROM analytics_events WHERE 1=1"""
    args = []
    if start_iso: sql += " AND ts >= ?"; args.append(start_iso)
    if end_iso:   sql += " AND ts <= ?"; args.append(end_iso)
    if uf:        sql += " AND uf = ?";  args.append(uf)
    if municipio: sql += " AND municipio = ?"; args.append(municipio)
    if gender:    sql += " AND gender = ?"; args.append(gender)
    sql += " ORDER BY ts DESC"
    rows = []
    with _conn() as cn:
        for r in cn.execute(sql, tuple(args)):
            rows.append({
                "ts": r[0], "kind": r[1], "policy": r[2], "uf": r[3], "municipio": r[4],
                "query": r[5], "gender": r[6],
                "met": _json.loads(r[7]) if r[7] else None,
                "missing": _json.loads(r[8]) if r[8] else None,
                "extras": _json.loads(r[9]) if r[9] else None,
            })
    return rows

def migrate_db():
    """Garante que a tabela profiles tenha updated_at e popula valores faltantes."""
    with _conn() as cn:
        # inspeciona colunas existentes
        cols = [r[1] for r in cn.execute("PRAGMA table_info(profiles)").fetchall()]
        # adiciona a coluna se não existir
        if "updated_at" not in cols:
            cn.execute("ALTER TABLE profiles ADD COLUMN updated_at TEXT;")
        if "created_at" not in cols:
            # Se por algum motivo created_at não existir (raro),
            # ainda assim evitamos quebrar criando a coluna.
            cn.execute("ALTER TABLE profiles ADD COLUMN created_at TEXT;")

        # preenche updated_at vazio com created_at
        try:
            cn.execute("""
                UPDATE profiles
                   SET updated_at = COALESCE(updated_at, created_at)
                 WHERE updated_at IS NULL OR updated_at = '';
            """)
        except Exception:
            # se created_at também estiver vazio, preenche com agora
            from datetime import datetime
            now = datetime.utcnow().isoformat()
            cn.execute("""
                UPDATE profiles
                   SET updated_at = COALESCE(updated_at, ?),
                       created_at = COALESCE(created_at, ?)
                 WHERE updated_at IS NULL OR updated_at = '' OR created_at IS NULL OR created_at = '';
            """, (now, now))

from datetime import datetime

def create_person_account(name: str, username: str, password: str) -> int:
    now = datetime.utcnow().isoformat()
    pw = _hash_password(password)
    with _DB_LOCK:
        with _conn() as cn:
            cur = cn.execute("""
                INSERT INTO accounts (kind, username, display_name, password_hash, created_at)
                VALUES ('person', ?, ?, ?, ?)
            """, (username, name, pw, now))
            rid = cur.lastrowid or cn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return int(rid)

def create_collective_account(cnpj: str, contact: str, password: str) -> int:
    now = datetime.utcnow().isoformat()
    pw = _hash_password(password)
    with _DB_LOCK:
        with _conn() as cn:
            cur = cn.execute("""
                INSERT INTO accounts (kind, cnpj, contact, password_hash, created_at)
                VALUES ('collective', ?, ?, ?, ?)
            """, (cnpj, contact, pw, now))
            rid = cur.lastrowid or cn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return int(rid)

def authenticate_person(username: str, password: str):
    with _conn() as cn:
        row = cn.execute("""
            SELECT id, kind, username, display_name, password_hash
              FROM accounts WHERE kind='person' AND username=?
        """, (username,)).fetchone()
        if not row: return None
        if _verify_password(password, row[4]):
            return {"id": row[0], "kind": row[1], "username": row[2], "display_name": row[3]}
        return None

def authenticate_collective(cnpj: str, password: str):
    with _conn() as cn:
        row = cn.execute("""
            SELECT id, kind, cnpj, contact, password_hash
              FROM accounts WHERE kind='collective' AND cnpj=?
        """, (cnpj,)).fetchone()
        if not row: return None
        if _verify_password(password, row[4]):
            return {"id": row[0], "kind": row[1], "cnpj": row[2], "contact": row[3]}
        return None

def ensure_user(user_id: str, name: Optional[str] = None):
    with _DB_LOCK:
        with _conn() as cn:
            cur = cn.execute("SELECT id FROM users WHERE id = ?", (user_id,))
            if not cur.fetchone():
                cn.execute(
                    "INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)",
                    (user_id, name or "", datetime.utcnow().isoformat())
                )

def save_profile_for_account(owner_account_id: int, profile: Dict[str, Any]) -> int:
    now = datetime.utcnow().isoformat()
    with _DB_LOCK:
        with _conn() as cn:
            last_ver = cn.execute("""
                SELECT COALESCE(MAX(version), 0) FROM profiles WHERE owner_account_id = ?
            """, (owner_account_id,)).fetchone()[0]
            version = (last_ver or 0) + 1
            cur = cn.execute("""
                INSERT INTO profiles (user_id, profile_json, version, created_at, updated_at, owner_account_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("", json.dumps(profile, ensure_ascii=False), version, now, now, owner_account_id))
            rid = cur.lastrowid or cn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return int(rid)

def update_profile_for_account(profile_id: int, owner_account_id: int, profile: Dict[str, Any]) -> None:
    now = datetime.utcnow().isoformat()
    with _DB_LOCK:
        with _conn() as cn:
            row = cn.execute("SELECT owner_account_id FROM profiles WHERE id=?", (profile_id,)).fetchone()
            if not row or row[0] != owner_account_id:
                raise PermissionError("Este perfil não pertence à sua conta.")
            cn.execute("""
                UPDATE profiles SET profile_json=?, updated_at=? WHERE id=?
            """, (json.dumps(profile, ensure_ascii=False), now, profile_id))

def get_profiles_by_account(owner_account_id: int) -> List[Tuple[int,int,str,str]]:
    with _conn() as cn:
        cur = cn.execute("""
            SELECT id, version, created_at, updated_at
              FROM profiles
             WHERE owner_account_id=?
             ORDER BY version DESC
        """, (owner_account_id,))
        return cur.fetchall()

def save_profile(user_id: str, profile: Dict[str, Any]) -> int:
    now = datetime.utcnow().isoformat()
    with _DB_LOCK:
        with _conn() as cn:
            last_ver = cn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM profiles WHERE user_id = ?",
                (user_id,)
            ).fetchone()[0]
            version = (last_ver or 0) + 1
            cur = cn.execute(
                "INSERT INTO profiles (user_id, profile_json, version, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, json.dumps(profile, ensure_ascii=False), version, now, now)
            )
            rid = cur.lastrowid or cn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return int(rid)

def update_profile(profile_id: int, profile: Dict[str, Any]):
    now = datetime.utcnow().isoformat()
    with _DB_LOCK:
        with _conn() as cn:
            cn.execute(
                "UPDATE profiles SET profile_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(profile, ensure_ascii=False), now, profile_id)
            )

def get_profiles(user_id: str) -> List[Tuple[int, int, str, str]]:
    with _conn() as cn:
        cur = cn.execute(
            "SELECT id, version, created_at, updated_at FROM profiles WHERE user_id = ? ORDER BY version DESC",
            (user_id,)
        )
        return cur.fetchall()

def load_profile(profile_id: int) -> Dict[str, Any]:
    with _conn() as cn:
        cur = cn.execute("SELECT profile_json FROM profiles WHERE id = ?", (profile_id,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else {}

def save_eligibility(user_id: str, profile_id: int, desired_policy: Optional[str],
                     matched_policies: List[Dict[str, Any]], gaps: List[Dict[str, Any]]) -> int:
    now = datetime.utcnow().isoformat()
    with _DB_LOCK:
        with _conn() as cn:
            cur = cn.execute(
                """INSERT INTO eligibility_results
                   (user_id, profile_id, desired_policy, matched_policies_json, gaps_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, profile_id, desired_policy or "",
                 json.dumps(matched_policies, ensure_ascii=False),
                 json.dumps(gaps, ensure_ascii=False),
                 now)
            )
            rid = cur.lastrowid or cn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return int(rid)