import argparse
import json
import logging
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
import requests

OAI_BASE = "https://export.arxiv.org/oai2"
NS_OAI   = "http://www.openarchives.org/OAI/2.0/"
NS_ARXIV = "http://arxiv.org/OAI/arXiv/"

_PHYSICS_SUBAREAS = {
    "astro-ph", "cond-mat", "gr-qc", "hep-ex", "hep-lat",
    "hep-ph", "hep-th", "math-ph", "nlin", "nucl-ex",
    "nucl-th", "physics", "quant-ph",
}

log = logging.getLogger(__name__)


def to_oai_set(name: str) -> str:
    """Convert arXiv dot notation (cs.CV) to OAI-PMH set spec (cs:cs:CV)."""
    if ":" in name:
        return name
    if "." in name:
        prefix, suffix = name.split(".", 1)
        if prefix in _PHYSICS_SUBAREAS:
            return f"physics:{prefix}:{suffix}"
        return f"{prefix}:{prefix}:{suffix}"
    if name in _PHYSICS_SUBAREAS:
        return f"physics:{name}"
    return name


class NoRecordsError(Exception):
    pass


def _text(elem, tag: str, ns: str = NS_ARXIV) -> str | None:
    child = elem.find(f"{{{ns}}}{tag}")
    if child is None or not child.text:
        return None
    return re.sub(r"\s+", " ", child.text).strip()


def parse_authors(authors_elem) -> list[dict]:
    if authors_elem is None:
        return []
    result = []
    for author in authors_elem.findall(f"{{{NS_ARXIV}}}author"):
        result.append({
            "keyname":   _text(author, "keyname"),
            "forenames": _text(author, "forenames"),
        })
    return result


def parse_records(xml_text: str) -> tuple[list[dict], str | None]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        log.error("XML parse error. Response excerpt: %s", xml_text[:200])
        raise

    error_elem = root.find(f".//{{{NS_OAI}}}error")
    if error_elem is not None:
        code = error_elem.get("code", "unknown")
        msg  = (error_elem.text or "").strip()
        if code == "noRecordsMatch":
            raise NoRecordsError(msg)
        raise ValueError(f"OAI-PMH error [{code}]: {msg}")

    records = []
    for record in root.findall(f"{{{NS_OAI}}}ListRecords/{{{NS_OAI}}}record"):
        header = record.find(f"{{{NS_OAI}}}header")
        if header is None:
            continue
        if header.get("status") == "deleted":
            continue

        oai_id    = _text(header, "identifier", ns=NS_OAI) or ""
        datestamp = _text(header, "datestamp",  ns=NS_OAI)
        arxiv_id  = oai_id.replace("oai:arXiv.org:", "")

        metadata = record.find(f"{{{NS_OAI}}}metadata")
        arxiv    = metadata.find(f"{{{NS_ARXIV}}}arXiv") if metadata is not None else None

        categories_raw = _text(arxiv, "categories") if arxiv is not None else None
        categories = categories_raw.split() if categories_raw else []

        records.append({
            "id":          arxiv_id,
            "created":     _text(arxiv, "created")     if arxiv is not None else None,
            "updated":     _text(arxiv, "updated")     if arxiv is not None else None,
            "title":       _text(arxiv, "title")       if arxiv is not None else None,
            "authors":     parse_authors(arxiv.find(f"{{{NS_ARXIV}}}authors") if arxiv is not None else None),
            "abstract":    _text(arxiv, "abstract")    if arxiv is not None else None,
            "categories":  categories,
            "comments":    _text(arxiv, "comments")    if arxiv is not None else None,
            "journal_ref": _text(arxiv, "journal-ref") if arxiv is not None else None,
            "doi":         _text(arxiv, "doi")         if arxiv is not None else None,
            "license":     _text(arxiv, "license")     if arxiv is not None else None,
            "msc_class":   _text(arxiv, "msc-class")   if arxiv is not None else None,
            "acm_class":   _text(arxiv, "acm-class")   if arxiv is not None else None,
            "datestamp":   datestamp,
        })

    token_elem = root.find(f".//{{{NS_OAI}}}resumptionToken")
    token = None
    if token_elem is not None and token_elem.text and token_elem.text.strip():
        token = token_elem.text.strip()

    return records, token


def fetch_page(params: dict, max_retries: int = 5) -> str:
    for attempt in range(max_retries):
        try:
            resp = requests.get(OAI_BASE, params=params, timeout=60)
        except requests.RequestException as exc:
            wait = 2 ** attempt * 10
            log.warning("Network error (attempt %d/%d): %s. Retrying in %ds.", attempt + 1, max_retries, exc, wait)
            if attempt == max_retries - 1:
                raise
            time.sleep(wait)
            continue

        if resp.status_code == 503:
            wait = int(resp.headers.get("Retry-After", 30))
            sys.stderr.write(f"\n  [503] Backing off {wait}s (attempt {attempt+1}/{max_retries})...\n")
            if attempt == max_retries - 1:
                raise RuntimeError(f"Exceeded max retries on 503 for params={params}")
            time.sleep(wait)
            continue

        resp.raise_for_status()
        return resp.text

    raise RuntimeError("Exhausted retries")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
            id           TEXT PRIMARY KEY,
            created      TEXT,
            updated      TEXT,
            title        TEXT,
            abstract     TEXT,
            authors      TEXT,
            comments     TEXT,
            journal_ref  TEXT,
            doi          TEXT,
            license      TEXT,
            msc_class    TEXT,
            acm_class    TEXT,
            datestamp    TEXT
        );
        CREATE TABLE IF NOT EXISTS paper_categories (
            paper_id TEXT REFERENCES papers(id),
            category TEXT,
            PRIMARY KEY (paper_id, category)
        );
        CREATE INDEX IF NOT EXISTS idx_papers_created ON papers(created);
        CREATE INDEX IF NOT EXISTS idx_cat_category   ON paper_categories(category);
    """)
    # Migrate existing databases that predate these columns
    for col in ("msc_class", "acm_class"):
        try:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


def insert_page(conn: sqlite3.Connection, records: list[dict]) -> tuple[int, int]:
    ids = [r["id"] for r in records]
    existing = {
        row[0] for row in conn.execute(
            f"SELECT id FROM papers WHERE id IN ({','.join('?'*len(ids))})", ids
        )
    }

    paper_rows = [
        (
            r["id"], r["created"], r["updated"], r["title"], r["abstract"],
            json.dumps(r["authors"], ensure_ascii=False),
            r["comments"], r["journal_ref"], r["doi"], r["license"],
            r["msc_class"], r["acm_class"], r["datestamp"],
        )
        for r in records
    ]
    conn.executemany(
        """
        INSERT INTO papers(id,created,updated,title,abstract,authors,comments,journal_ref,doi,license,msc_class,acm_class,datestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            created=excluded.created, updated=excluded.updated,
            title=excluded.title, abstract=excluded.abstract,
            authors=excluded.authors, comments=excluded.comments,
            journal_ref=excluded.journal_ref, doi=excluded.doi,
            license=excluded.license, msc_class=excluded.msc_class,
            acm_class=excluded.acm_class, datestamp=excluded.datestamp
        WHERE excluded.datestamp > papers.datestamp
        """,
        paper_rows,
    )

    cat_rows = [(r["id"], cat) for r in records for cat in r["categories"]]
    if cat_rows:
        conn.executemany("INSERT OR IGNORE INTO paper_categories VALUES (?,?)", cat_rows)

    conn.commit()
    new_count     = sum(1 for r in records if r["id"] not in existing)
    updated_count = sum(1 for r in records if r["id"] in existing)
    return new_count, updated_count


def harvest_set(
    set_name: str,
    from_date: str,
    until_date: str | None,
    conn: sqlite3.Connection,
    delay: float,
) -> int:
    oai_set = to_oai_set(set_name)
    params: dict = {
        "verb":           "ListRecords",
        "metadataPrefix": "arXiv",
        "set":            oai_set,
        "from":           from_date,
    }
    if until_date:
        params["until"] = until_date

    inserted = 0
    updated  = 0
    page     = 0

    while True:
        page += 1
        xml_text = fetch_page(params)

        try:
            records, token = parse_records(xml_text)
        except NoRecordsError:
            sys.stderr.write(f"[{set_name}] No records found for this date range.\n")
            return 0

        new, upd = insert_page(conn, records)
        inserted += new
        updated  += upd

        sys.stderr.write(
            f"[{set_name}] page {page} | inserted: {inserted} | updated: {updated}\n"
        )

        if token is None:
            break

        params = {"verb": "ListRecords", "resumptionToken": token}
        time.sleep(delay)

    sys.stderr.write(
        f"\n[{set_name}] DONE — {inserted:,} inserted, {updated:,} updated, {page} pages\n"
    )
    return inserted


def parse_args():
    parser = argparse.ArgumentParser(
        description="Harvest arXiv metadata via OAI-PMH into a SQLite database."
    )
    parser.add_argument(
        "--sets", nargs="+", required=True, metavar="SET",
        help="arXiv set names, e.g. cs.AI cs.LG math.CO"
    )
    parser.add_argument(
        "--from-date", required=True, metavar="YYYY-MM-DD",
        help="Harvest records from this date (inclusive)"
    )
    parser.add_argument(
        "--until-date", default=None, metavar="YYYY-MM-DD",
        help="Harvest records up to this date (inclusive)"
    )
    parser.add_argument(
        "--db", default="papers.db", metavar="FILE",
        help="SQLite database file (created if absent, default: papers.db)"
    )
    parser.add_argument(
        "--delay", type=float, default=5.0, metavar="SECONDS",
        help="Seconds to wait between requests (default: 5.0)"
    )
    parser.add_argument(
        "--log-level", default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    args = parser.parse_args()

    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if not date_re.match(args.from_date):
        parser.error(f"--from-date must be YYYY-MM-DD, got: {args.from_date}")
    if args.until_date and not date_re.match(args.until_date):
        parser.error(f"--until-date must be YYYY-MM-DD, got: {args.until_date}")
    if args.until_date and args.from_date > args.until_date:
        parser.error("--from-date must not be after --until-date")

    return args


def main():
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), stream=sys.stderr)

    total = 0

    sys.stderr.write(
        f"Harvesting sets: {', '.join(args.sets)}\n"
        f"Date range:      {args.from_date} → {args.until_date or 'now'}\n"
        f"Database:        {args.db}\n\n"
    )

    with sqlite3.connect(args.db) as conn:
        init_db(conn)
        for set_name in args.sets:
            try:
                count = harvest_set(
                    set_name=set_name,
                    from_date=args.from_date,
                    until_date=args.until_date,
                    conn=conn,
                    delay=args.delay,
                )
                total += count
            except KeyboardInterrupt:
                sys.stderr.write(f"\nInterrupted. {total:,} records inserted so far.\n")
                sys.exit(1)

    sys.stderr.write(
        f"\nHarvest complete: {len(args.sets)} set(s), {total:,} new records → {args.db}\n"
    )


if __name__ == "__main__":
    main()
