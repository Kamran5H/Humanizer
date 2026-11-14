"""
Humanizer Headless CLI Interface.
Supports file processing (.docx, .txt), stdin pipelines, score reporting,
live console percentage progress bar, and executive audit reports.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from humanizer import __version__
from humanizer.config import HumanizerConfig
from humanizer.detector import analyze, score_text
from humanizer.document import docx_text, extract_text_from_file
from humanizer.engine import humanize_docx, humanize_text
from humanizer.lint import lint_text


def render_cli_progress_bar(current: int, total: int, pct: float, message: str, width: int = 24) -> None:
    """Render a live ASCII percentage progress bar directly to stderr."""
    if total <= 0:
        return
    filled = int(width * (pct / 100.0))
    filled = max(0, min(width, filled))
    bar = "█" * filled + "░" * (width - filled)
    msg_clean = message.replace("\n", " ")[:32]
    sys.stderr.write(f"\r  [{bar}] {pct:5.1f}% ({current}/{total}) | {msg_clean:<32}")
    sys.stderr.flush()
    if current >= total:
        sys.stderr.write("\n")
        sys.stderr.flush()


def print_cli_report(
    initial_score: float,
    final_score: float,
    words: int,
    paras: int,
    metrics: dict | None = None,
    dst: Path | None = None,
    duration: float = 0.0,
) -> None:
    """Print a compact, aligned summary box to stderr."""
    metrics = metrics or {}
    width = 64
    delta = initial_score - final_score
    rows = [
        ("Verdict", metrics.get("verdict") or "Humanized"),
        ("Pattern index", f"{initial_score:.1f}/100 -> {final_score:.1f}/100" + (f"  (-{delta:.1f} pts)" if delta > 0 else "")),
        ("Paragraphs", str(paras)),
        ("Words", f"{words:,}"),
        ("Length variation", f"{metrics.get('burstiness') or 0.0:.1f}%"),
        ("AI patterns left", str(metrics.get("ai_tells", 0))),
    ]
    ppl = metrics.get("perplexity")
    if isinstance(ppl, (int, float)):
        rows.append(("Perplexity", f"{ppl:.1f}"))
    if duration > 0:
        rows.append(("Time", f"{duration:.1f}s"))
    if dst:
        rows.append(("Saved to", str(dst.name)))

    def line(text: str) -> str:
        return "│ " + text[: width - 4].ljust(width - 4) + " │"

    out = ["┌" + "─" * (width - 2) + "┐", line("HUMANIZER PRO — REPORT".center(width - 4)), "├" + "─" * (width - 2) + "┤"]
    out += [line(f"{k:<18}{v}") for k, v in rows]
    out.append("└" + "─" * (width - 2) + "┘")
    print("\n" + "\n".join(out) + "\n", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="humanizer",
        description="Humanizer Pro — rewrite AI-sounding text into natural prose.",
        epilog="Examples:\n  humanizer essay.docx\n  humanizer notes.txt -o clean.txt --general\n"
               "  cat draft.md | humanizer --stdin --local > out.md\n  humanizer --analyze report.txt\n  humanizer --web",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("file", nargs="?", help="Input file (.docx, .txt, .md, .pdf)")
    p.add_argument("--text", help="Raw input string to humanize")
    p.add_argument("--stdin", action="store_true", help="Read input from stdin")
    p.add_argument("--out", "-o", help="Output path (default: <name>_humanized.<ext>)")
    p.add_argument("--level", type=int, choices=[1, 2, 3], default=3, help="Strength: 1=light, 2=balanced, 3=max (default)")
    p.add_argument("--target", type=float, default=10.0, help="Stop early at this local AI score (default 10)")
    p.add_argument("--budget", type=int, default=4, help="LLM candidates per paragraph at level>=2 (default 4)")
    p.add_argument("--scientific", "--academic", dest="scientific", action="store_true", default=True, help="Academic register (default)")
    p.add_argument("--general", dest="scientific", action="store_false", help="General/conversational register")
    p.add_argument("--local", "--offline", dest="local", action="store_true", help="Offline rule engine only (nothing leaves your machine)")
    p.add_argument("--analyze", action="store_true", help="Only score the input and list AI patterns; do not rewrite")
    p.add_argument("--score", action="store_true", help="Print the score report (same as --report)")
    p.add_argument("--report", action="store_true", default=True, help="Print the report box to stderr (default)")
    p.add_argument("--no-report", dest="report", action="store_false", help="Suppress the report box")
    p.add_argument("--json", action="store_true", help="Print results as JSON")
    p.add_argument("--workers", type=int, default=4, help="Parallel LLM workers (default 4)")
    p.add_argument("--seed", type=int, help="Random seed for reproducible offline output")
    p.add_argument("--web", action="store_true", help="Start the web studio")
    p.add_argument("--gui", action="store_true", help="Start the desktop app")
    p.add_argument("--port", type=int, default=8000, help="Web studio port (default 8000)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _print_analysis(text: str, as_json: bool) -> int:
    a = analyze(text)
    if as_json:
        print(json.dumps({k: v for k, v in a.items() if k != "sentence_scores"}, indent=2))
        return 0
    print(f"Writing-pattern index: {a['ai_score']:.1f}/100  ({a['verdict']})")
    print(a["score_explanation"])
    for s in a["signals"]:
        print(f"  {s['label']:<20} {s['ai']:>3}%  {s['hint']}")
    if a["tells"]:
        print(f"\n{len(a['tells'])} AI pattern(s):")
        for t in a["tells"][:40]:
            print(f"  - \"{t['text'].strip()}\" ({t['label']}) -> {t['suggestion']}")
    return 0


def _read_input(args) -> tuple[str, Path | None]:
    if args.stdin:
        return sys.stdin.read(), None
    if args.text is not None:
        return args.text, None
    src = Path(args.file)
    if not src.exists():
        raise FileNotFoundError(f"File '{src}' does not exist.")
    return extract_text_from_file(src), src


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # never crash on a legacy Windows console
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.web or (not args.file and args.text is None and not args.stdin and not args.gui):
        if not args.web:
            try:
                import tkinter  # noqa: F401

                from humanizer.gui import launch_gui

                launch_gui()
                return 0
            except Exception:
                pass
        from humanizer.web import launch_web

        launch_web(port=args.port)
        return 0
    if args.gui:
        from humanizer.gui import launch_gui

        launch_gui()
        return 0

    try:
        input_text, src = _read_input(args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.analyze:
        return _print_analysis(input_text, args.json)

    show_bar = not args.json and sys.stderr.isatty()
    cfg = HumanizerConfig(
        use_gemini=not args.local,
        stealth_level=args.level,
        target_pct=args.target,
        retry_budget=args.budget,
        scientific=args.scientific,
        parallel_workers=args.workers,
        progress_callback=render_cli_progress_bar if show_bar else None,
    )
    if args.seed is not None:
        cfg.rng.seed(args.seed)

    t0 = time.time()
    init_res = score_text(input_text)
    dst: Path | None = None
    if src is not None and src.suffix.lower() == ".docx":
        dst = Path(args.out) if args.out else src.with_name(f"{src.stem}_humanized.docx")
        humanize_docx(src, cfg, dst=dst)
        out_text = docx_text(dst)
    else:
        out_text = humanize_text(input_text, cfg)
        if src is not None:
            ext = ".txt" if src.suffix.lower() == ".pdf" else src.suffix
            dst = Path(args.out) if args.out else src.with_name(f"{src.stem}_humanized{ext}")
            dst.write_text(out_text, encoding="utf-8")
        elif args.out:
            dst = Path(args.out)
            dst.write_text(out_text, encoding="utf-8")
    scores = score_text(out_text)
    dur = time.time() - t0

    if args.json:
        print(json.dumps({
            "success": True,
            "input_file": str(src) if src else None,
            "output_file": str(dst) if dst else None,
            "output": out_text if dst is None else None,
            "engine": cfg.engine_used,
            "initial_score": init_res.get("ai_score", 0.0),
            "final_score": scores.get("ai_score", 0.0),
            "metrics": scores,
            "integrity": lint_text(out_text, input_text),
            "duration_seconds": round(dur, 2),
        }, indent=2))
        return 0
    if dst is None:
        sys.stdout.write(out_text if out_text.endswith("\n") else out_text + "\n")
    if args.report or args.score:
        paras = len([p for p in out_text.split("\n\n") if p.strip()])
        print_cli_report(init_res["ai_score"], scores["ai_score"], len(out_text.split()), paras, scores, dst=dst, duration=dur)
    elif dst is not None:
        print(f"Saved: {dst}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
