"""Accès à la base : une seule porte d'entrée pour toute la chaîne.

La cible de production est PostgreSQL 16, pilotée par ``psycopg`` (v3). Un
second pilote, ``sqlite3`` de la bibliothèque standard, permet d'exécuter la
chaîne complète et les tests sans aucune infrastructure — c'est le « mode
démonstration » décrit dans le README. Le SQL applicatif est écrit en style
``%s`` (PostgreSQL) et traduit à la volée pour SQLite, de sorte qu'une seule
version de chaque requête existe dans le dépôt.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .config import config

PREFIXES_POSTGRES = ("postgresql://", "postgres://")


def est_postgres(url: str | None = None) -> bool:
    return (url or config.db_url).startswith(PREFIXES_POSTGRES)


def connecter(url: str | None = None):
    """Ouvre une connexion DB-API 2.0 vers PostgreSQL ou SQLite."""
    url = url or config.db_url
    if est_postgres(url):
        try:
            import psycopg  # noqa: PLC0415  (import tardif : optionnel en mode démo)
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError(
                "Le pilote 'psycopg[binary]' est requis pour PostgreSQL "
                "(pip install -r requirements.txt), ou utilisez DB_URL=sqlite:///…"
            ) from exc
        return psycopg.connect(url)

    if url.startswith("sqlite:///"):
        chemin = Path(url[len("sqlite:///") :])
        chemin.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(chemin, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    raise ValueError(f"URL de base non supportée : {url!r}")


def adapter(sql: str, url: str | None = None) -> str:
    """Traduit les marqueurs de paramètres ``%s`` vers ``?`` pour SQLite."""
    return sql if est_postgres(url) else sql.replace("%s", "?")


def executer(cur, sql: str, params: Sequence[Any] | None = None, url: str | None = None):
    cur.execute(adapter(sql, url), tuple(params or ()))
    return cur


def executer_lot(cur, sql: str, lignes: Sequence[Sequence[Any]], url: str | None = None):
    cur.executemany(adapter(sql, url), [tuple(l) for l in lignes])
    return cur


@contextmanager
def transaction(url: str | None = None) -> Iterator[Any]:
    """Ouvre une transaction : commit si tout passe, rollback sinon."""
    url = url or config.db_url
    conn = connecter(url)
    try:
        if not est_postgres(url):
            conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def executer_script(chemin: Path, url: str | None = None) -> None:
    """Exécute un fichier .sql complet (DDL, GRANT, purge…)."""
    url = url or config.db_url
    sql = Path(chemin).read_text(encoding="utf-8")
    conn = connecter(url)
    try:
        if est_postgres(url):
            with conn.cursor() as cur:
                cur.execute(sql)
        else:
            conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def lire(sql: str, params: Sequence[Any] | None = None, url: str | None = None) -> list[dict]:
    """Exécute un SELECT et renvoie une liste de dictionnaires."""
    url = url or config.db_url
    conn = connecter(url)
    try:
        cur = conn.cursor()
        executer(cur, sql, params, url)
        colonnes = [d[0] for d in cur.description]
        return [dict(zip(colonnes, ligne)) for ligne in cur.fetchall()]
    finally:
        conn.close()
