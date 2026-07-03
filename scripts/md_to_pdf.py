"""
Convert a Markdown doc to a styled PDF.
Usage:  python md_to_pdf.py COST_ANALYSIS.md  [output.pdf]
"""
import re
import sys

import markdown
from xhtml2pdf import pisa

CSS = """
@page { size: A4; margin: 1.6cm 1.5cm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1a1a1a; line-height: 1.5; }
h1 { font-size: 19pt; color: #1d4ed8; border-bottom: 2px solid #1d4ed8; padding-bottom: 4px; margin-top: 0; }
h2 { font-size: 13.5pt; color: #111; margin-top: 16px; border-bottom: 1px solid #e5e7eb; padding-bottom: 3px; }
h3 { font-size: 11.5pt; color: #1d4ed8; margin-top: 12px; }
p, li { font-size: 10pt; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; }
th { background-color: #1d4ed8; color: #fff; font-size: 8.5pt; padding: 5px 7px; text-align: left; }
td { border-bottom: 1px solid #e5e7eb; padding: 4px 7px; font-size: 8.5pt; }
tr:nth-child(even) td { background-color: #f7f8fa; }
code { background-color: #f1f3f5; font-family: Courier, monospace; font-size: 8.5pt; padding: 1px 3px; }
pre { background-color: #f7f8fa; border: 1px solid #e5e7eb; border-radius: 4px; padding: 8px;
      font-family: Courier, monospace; font-size: 8pt; white-space: pre-wrap; }
blockquote { border-left: 3px solid #f59e0b; background: #fffbeb; margin: 8px 0; padding: 6px 10px; color: #92400e; }
strong { color: #111; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 12px 0; }
"""

# Map the few emoji we use to plain markers reportlab can render
EMOJI = {
    "⚠️": "[!]", "✅": "[OK]", "⬜": "[ ]", "📖": "", "💰": "", "📈": "", "🎙️": "",
    "🔧": "", "📦": "", "⚡": "", "📱": "", "🟢": "", "🎯": "", "→": "->", "·": "-",
    "₹": "Rs ", "✓": "v", "✕": "x", "●": "*", "○": "o",
}


def strip_emoji(text: str) -> str:
    for k, v in EMOJI.items():
        text = text.replace(k, v)
    # remove any remaining non-latin pictographs
    return re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "COST_ANALYSIS.md"
    out = sys.argv[2] if len(sys.argv) > 2 else src.rsplit(".", 1)[0] + ".pdf"

    with open(src, "r", encoding="utf-8") as f:
        md = strip_emoji(f.read())

    html_body = markdown.markdown(md, extensions=["tables", "fenced_code"])
    html = f"<html><head><style>{CSS}</style></head><body>{html_body}</body></html>"

    with open(out, "w+b") as f:
        status = pisa.CreatePDF(html, dest=f)
    print(("OK -> " if not status.err else "ERRORS in ") + out)


if __name__ == "__main__":
    main()
