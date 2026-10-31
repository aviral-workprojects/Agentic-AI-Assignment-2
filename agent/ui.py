"""Tiny UI helpers. Tables are rendered as Markdown so the app does not need pandas (st.dataframe/st.table import it, and some
locked-down Windows machines block pandas' compiled extensions)."""

def _cell(v):
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ").replace("\r", " ")[:200]

def md_table(rows, cols):
    """rows: list of dicts; cols: ordered keys. Returns a Markdown table (or an italic 'none' note)."""
    if not rows:
        return "_none_"
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "\n".join("| " + " | ".join(_cell(r.get(c)) for c in cols) + " |" for r in rows)
