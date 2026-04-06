import os
import re
import json
import html
import logging
from typing import List, Dict, Optional, Any
import chess
import chess.svg
from chess_tools.lib.models import CrucialMoment
from chess_tools.analysis.engine import TACTIC_LABELS, TACTIC_COLORS, MOMENT_TYPE_LABELS, SEVERITY_COLORS, MATE_SCORE_CP

logger = logging.getLogger("chess_transfer")

def generate_markdown_report(moments: List[CrucialMoment], metadata: Dict[str, str], output_dir: str = "analysis"):
    """
    Generates a Markdown report from the analyzed moments.

    Args:
        moments (List[CrucialMoment]): The list of analyzed moments.
        metadata (Dict[str, str]): Game metadata (White, Black, Date, etc.).
        output_dir (str): Directory to save the report and images.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # Create images subdirectory
    images_dir = os.path.join(output_dir, "images")
    if not os.path.exists(images_dir):
        os.makedirs(images_dir)

    # Create filename: Date_White_vs_Black.md
    safe_date = metadata['Date'].replace('.', '-')
    safe_white = "".join(c for c in metadata['White'] if c.isalnum() or c in (' ', '_')).replace(' ', '_')
    safe_black = "".join(c for c in metadata['Black'] if c.isalnum() or c in (' ', '_')).replace(' ', '_')
    filename = f"{safe_date}_{safe_white}_vs_{safe_black}.md"
    output_path = os.path.join(output_dir, filename)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# Analysis: {metadata['White']} vs {metadata['Black']}\n\n")
        f.write(f"**Date:** {metadata['Date']} | **Event:** {metadata['Event']} | **Site:** {metadata['Site']}\n\n")
        
        if not moments:
            f.write("No crucial moments (blunders/missed wins) detected for the hero in this game.\n")
            logger.info(f"Report generated (empty): {output_path}")
            return

        blunder_count = sum(1 for m in moments if m.moment_type == "blunder")
        missed_count = sum(1 for m in moments if m.moment_type in ("missed_chance", "missed_mate"))
        f.write(f"Found **{len(moments)}** crucial moments")
        if missed_count:
            f.write(f" ({blunder_count} blunder{'s' if blunder_count != 1 else ''}, {missed_count} missed opportunity{'s' if missed_count != 1 else ''})")
        f.write(".\n\n")

        for i, moment in enumerate(moments, 1):
            # Save SVG to file
            image_filename = f"{filename.replace('.md', '')}_moment_{i}.svg"
            image_path = os.path.join(images_dir, image_filename)

            if moment.svg_content:
                with open(image_path, "w") as img_file:
                    img_file.write(moment.svg_content)

            # Relative path for Markdown
            relative_image_path = f"images/{image_filename}"

            type_label = MOMENT_TYPE_LABELS.get(moment.moment_type, "Blunder")
            severity_label = moment.severity.upper()
            tactic_label = TACTIC_LABELS.get(moment.tactic_type, moment.tactic_type)
            is_missed = moment.moment_type in ("missed_chance", "missed_mate")
            if is_missed:
                tactic_label = f"Missed {tactic_label}"

            f.write(f"## Moment {i} — {type_label} [{severity_label}]\n\n")
            f.write(f"**Tactic:** {tactic_label}\n\n")
            f.write(f"![Position]({relative_image_path})\n\n")
            f.write(f"**FEN:** `{moment.fen}`\n\n")

            if is_missed:
                f.write(f"- **You Played:** **{moment.move_played_san}**\n")
                f.write(f"- **You Could Have Played:** **{moment.best_move_san}**\n")
                f.write(f"- **Eval Swing:** {moment.eval_swing} cp\n")
                if moment.best_line:
                    f.write(f"- **Best Line:** _{moment.best_line}_\n\n")
            else:
                f.write(f"- **You Played:** **{moment.move_played_san}** <span style='color:red'>(Red Arrow)</span>\n")
                f.write(f"- **Engine Best:** **{moment.best_move_san}** <span style='color:green'>(Green Arrow)</span>\n")
                f.write(f"- **Eval Swing:** {moment.eval_swing} cp\n")
                f.write(f"- **Variation:** _{moment.pv_line}_\n\n")

            if moment.tactical_alert:
                f.write(f"> **{moment.tactical_alert}**\n\n")

            if moment.refutation_line:
                f.write(f"**Refutation:** _{moment.refutation_line}_\n\n")

            f.write("---\n")
            
    logger.info(f"Report generated: {output_path}")


def generate_line_player_html(
    player_id: str,
    start_fen: str,
    line_san: str,
    hero_color: Optional[chess.Color],
    initial_move_san: Optional[str] = None,
    kind: str = "refutation",
    start_label: str = "Start",
) -> str:
    """
    Generate an interactive SVG board player for a line of moves.

    Args:
        player_id: Unique HTML id for the player element.
        start_fen: FEN of the position before the line starts.
        line_san: Space-separated SAN moves to step through.
        hero_color: Board orientation (hero's color).
        initial_move_san: If given, push this move first (e.g. blunder move).
        kind: "refutation" or "best-line" (controls CSS color theme).
        start_label: Caption shown for the initial (frame 0) position.

    Returns:
        HTML string for the interactive player.
    """
    if not line_san:
        return ""
    orientation = hero_color if hero_color is not None else chess.WHITE
    board = chess.Board(start_fen)

    # For blunders: push the blunder move first so frames show the refutation
    if initial_move_san:
        try:
            board.push(board.parse_san(initial_move_san))
        except Exception:
            pass

    frames = []
    svg0 = chess.svg.board(board, size=360, orientation=orientation, coordinates=True)
    frames.append((start_label, svg0))

    for san in line_san.split()[:6]:
        try:
            move = board.parse_san(san)
            # Caption: determine move number and side
            move_num = board.fullmove_number
            side = "..." if board.turn == chess.BLACK else "."
            caption = f"{move_num}{side} {san}"
            board.push(move)
            svg = chess.svg.board(board, size=360, orientation=orientation,
                                  coordinates=True, lastmove=move)
            frames.append((caption, svg))
        except Exception:
            break

    if len(frames) < 2:
        return ""

    frame_divs = "\n".join(
        f'      <div class="lp-frame" data-caption="{html.escape(caption)}">{svg}</div>'
        for caption, svg in frames
    )
    _, svg0_content = frames[0]
    caption0 = frames[0][0]
    total = len(frames) - 1

    return f"""<div class="line-player {kind}" id="{player_id}" data-total="{total}">
  <div class="lp-frames" hidden>
{frame_divs}
  </div>
  <div class="lp-stage">{svg0_content}</div>
  <div class="lp-caption">{html.escape(caption0)}</div>
  <div class="lp-controls">
    <button class="lp-btn lp-prev" aria-label="Previous" disabled>&#9664;</button>
    <button class="lp-btn lp-play" aria-label="Play">&#9654;</button>
    <button class="lp-btn lp-next" aria-label="Next">&#9654;</button>
    <span class="lp-counter">0/{total}</span>
  </div>
</div>"""


_HTML_STYLE = """
    :root {
        --bg-color: #1a1a1a;
        --text-color: #e0e0e0;
        --card-bg: #2a2a2a;
        --accent-color: #4a9eff;
        --muted-color: #a0a0a0;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        background-color: var(--bg-color);
        color: var(--text-color);
        padding: 20px;
        display: flex;
        flex-direction: column;
        align-items: center;
        min-height: 100vh;
    }
    .container { max-width: 800px; width: 100%; }
    a { color: var(--accent-color); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .back-link { margin-bottom: 20px; display: inline-block; font-size: 0.95rem; }
    h1 { margin-bottom: 8px; }
    .meta { color: var(--muted-color); margin-bottom: 24px; font-size: 0.95rem; }
    .summary-count { margin-bottom: 24px; font-size: 1.05rem; }
    .moment-card {
        background: var(--card-bg);
        border-radius: 10px;
        padding: 24px;
        margin-bottom: 24px;
    }
    .moment-card h2 { margin-bottom: 16px; font-size: 1.3rem; }
    .board-svg { display: flex; justify-content: center; margin-bottom: 16px; }
    .board-svg svg { max-width: 400px; width: 100%; height: auto; }
    .fen { font-family: monospace; font-size: 0.85rem; color: var(--muted-color); margin-bottom: 12px; word-break: break-all; }
    .move-info { margin-bottom: 12px; line-height: 1.7; }
    .move-played { color: #ff6b6b; font-weight: bold; }
    .move-best { color: #51cf66; font-weight: bold; }
    .eval-swing { color: var(--muted-color); }
    .variation { font-style: italic; color: var(--muted-color); }
    .tactical-alert {
        background: rgba(255, 107, 107, 0.15);
        border-left: 4px solid #ff6b6b;
        padding: 10px 14px;
        margin-bottom: 12px;
        border-radius: 0 6px 6px 0;
        font-weight: bold;
    }
    .empty-msg { color: var(--muted-color); font-style: italic; margin-top: 20px; }
    .severity-pill {
        display: inline-block;
        font-size: 0.7rem;
        font-weight: bold;
        padding: 2px 8px;
        border-radius: 10px;
        color: #fff;
        margin-left: 8px;
        vertical-align: middle;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .tactic-badge {
        display: inline-block;
        font-size: 0.8rem;
        font-weight: 600;
        padding: 3px 10px;
        border-radius: 6px;
        color: #fff;
        margin-bottom: 12px;
    }
    .moment-type-label { font-size: 0.9rem; color: var(--muted-color); margin-left: 4px; }
    .lichess-btn {
        display: inline-flex;
        align-items: center;
        background: #629924;
        color: #fff;
        padding: 8px 16px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 0.9rem;
        margin-bottom: 16px;
        text-decoration: none;
    }
    .lichess-btn:hover { background: #4e7a1d; text-decoration: none; }
    .moment-lichess-link { font-size: 0.8rem; margin-left: 8px; }
    /* Line player */
    .line-player {
        background: rgba(74, 158, 255, 0.08);
        border-left: 4px solid var(--accent-color);
        border-radius: 0 6px 6px 0;
        padding: 14px;
        margin-bottom: 16px;
    }
    .line-player .lp-stage { display: flex; justify-content: center; }
    .line-player .lp-stage svg { max-width: 360px; width: 100%; height: auto; }
    .line-player .lp-caption {
        text-align: center;
        font-family: monospace;
        font-size: 0.95rem;
        color: var(--accent-color);
        margin: 8px 0 10px;
        min-height: 1.2em;
    }
    .line-player .lp-controls {
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 8px;
    }
    .line-player .lp-btn {
        background: var(--card-bg);
        color: var(--text-color);
        border: 1px solid #3a3a3a;
        padding: 6px 14px;
        border-radius: 6px;
        font-size: 1rem;
        cursor: pointer;
        touch-action: manipulation;
    }
    .line-player .lp-btn:hover:not(:disabled) { background: #3a3a3a; }
    .line-player .lp-btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .line-player .lp-next { transform: none; }
    .line-player .lp-prev { transform: rotate(180deg); }
    .line-player .lp-counter {
        font-size: 0.85rem;
        color: var(--muted-color);
        margin-left: 8px;
        min-width: 40px;
        text-align: center;
    }
    .line-player.best-line { border-left-color: #51cf66; background: rgba(81,207,102,0.08); }
    .line-player.refutation { border-left-color: #ff6b6b; background: rgba(255,107,107,0.08); }

    /* Index page styles */
    .report-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
        gap: 16px;
        margin-top: 24px;
    }
    .report-card {
        background: var(--card-bg);
        border-radius: 10px;
        padding: 20px;
        transition: transform 0.15s, box-shadow 0.15s;
    }
    .report-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.4); }
    .report-card h3 { margin-bottom: 8px; font-size: 1.1rem; }
    .report-card .card-meta { color: var(--muted-color); font-size: 0.9rem; margin-bottom: 10px; }
    .report-card .card-moments { color: var(--accent-color); font-weight: bold; }
    .no-reports { color: var(--muted-color); text-align: center; margin-top: 40px; font-style: italic; }

    /* Run-analysis button */
    .run-bar {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 24px;
    }
    .run-btn {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: var(--accent-color);
        color: #fff;
        border: none;
        padding: 9px 18px;
        border-radius: 6px;
        font-size: 0.95rem;
        font-weight: 600;
        cursor: pointer;
        transition: background 0.15s, opacity 0.15s;
    }
    .run-btn:hover:not(:disabled) { background: #2d7fd4; }
    .run-btn:disabled { opacity: 0.55; cursor: not-allowed; }
    .run-status { font-size: 0.88rem; color: var(--muted-color); }
    .run-status.ok { color: #51cf66; }
    .run-status.err { color: #ff6b6b; }
    .token-link { font-size: 0.8rem; color: var(--muted-color); cursor: pointer; }
    .token-link:hover { color: var(--text-color); }
    .drills-btn {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: #51cf66;
        color: #111;
        border: none;
        padding: 9px 18px;
        border-radius: 6px;
        font-size: 0.95rem;
        font-weight: 600;
        cursor: pointer;
        text-decoration: none;
        transition: background 0.15s;
    }
    .drills-btn:hover { background: #3bb852; text-decoration: none; }

    /* iOS / Mobile Safari optimizations */
    * { -webkit-tap-highlight-color: transparent; }
    button, select, a { touch-action: manipulation; }
    body { padding-bottom: env(safe-area-inset-bottom); }
    select, input { font-size: max(16px, 0.95rem); }
    @media (max-width: 540px) {
        .report-grid { grid-template-columns: 1fr; }
        .moment-card { padding: 16px; }
        .board-svg svg { max-width: 100%; }
        .report-card { padding: 16px; }
        .report-card h3 { font-size: 1rem; }
    }
"""


def generate_annotated_pgn(metadata: Dict[str, str],
                           move_evals: List[Dict[str, Any]],
                           moments: List[CrucialMoment]) -> str:
    """
    Generate PGN string with eval annotations at every move.

    Args:
        metadata: Game headers.
        move_evals: Per-half-move eval list from analyze_game.
        moments: List of critical moments for NAG/comment annotation.

    Returns:
        Complete PGN string with headers and annotated moves.
    """
    if not move_evals:
        return ""

    # Build moment lookup by half_move
    moment_by_hm: Dict[int, CrucialMoment] = {}
    for entry in move_evals:
        if "moment_index" in entry:
            moment_by_hm[entry["half_move"]] = moments[entry["moment_index"]]

    lines = []
    # Headers
    for tag in ["Event", "Site", "Date", "White", "Black", "Result"]:
        val = metadata.get(tag, "?")
        lines.append(f'[{tag} "{val}"]')
    lines.append("")

    # Moves
    move_parts = []
    for entry in move_evals:
        hm = entry["half_move"]
        san = entry["san"]
        is_white = entry["is_white"]

        # Move number prefix
        if is_white:
            move_num = (hm + 1) // 2
            prefix = f"{move_num}. "
        else:
            prefix = ""

        # Eval comment
        if entry["mate_in"] is not None:
            mate_val = entry["mate_in"]
            eval_str = f"#{mate_val}" if mate_val > 0 else f"#-{abs(mate_val)}"
        else:
            pawns = entry["eval_cp"] / 100
            eval_str = f"{pawns:+.2f}"

        # Check for moment annotation
        nag = ""
        moment_comment = ""
        m = moment_by_hm.get(hm)
        if m:
            tactic_label = TACTIC_LABELS.get(m.tactic_type, m.tactic_type)
            if m.moment_type == "blunder":
                nag = " $4"
                ref_move = m.refutation_line.split()[0] if m.refutation_line else ""
                moment_comment = f" {tactic_label}"
                if ref_move:
                    moment_comment += f" · {ref_move}"
            elif m.moment_type == "missed_mate":
                nag = " $4"
                best = m.best_move_san if m.best_move_san != "N/A" else ""
                moment_comment = f" Missed Mate in {m.mate_in or '?'}"
                if best:
                    moment_comment += f" · best: {best}"
            elif m.moment_type == "missed_chance":
                nag = " $6"
                best = m.best_move_san if m.best_move_san != "N/A" else ""
                moment_comment = f" Missed {tactic_label}"
                if best:
                    moment_comment += f" · best: {best}"

        comment = f"{{{eval_str}{moment_comment}}}"
        move_parts.append(f"{prefix}{san}{nag} {comment}")

    # Wrap at ~80 chars
    result = metadata.get("Result", "*")
    move_text = " ".join(move_parts) + f" {result}"
    wrapped = []
    current_line = ""
    for word in move_text.split(" "):
        if len(current_line) + len(word) + 1 > 80 and current_line:
            wrapped.append(current_line)
            current_line = word
        else:
            current_line = f"{current_line} {word}" if current_line else word
    if current_line:
        wrapped.append(current_line)

    lines.append("\n".join(wrapped))
    return "\n".join(lines)


def generate_eval_chart_svg(move_evals: List[Dict[str, Any]],
                            width: int = 800, height: int = 300) -> str:
    """
    Generate an inline SVG evaluation chart.

    Args:
        move_evals: Per-half-move eval list with eval_cp, mate_in, moment_type.
        width: SVG width.
        height: SVG height.

    Returns:
        SVG string to embed in the HTML report.
    """
    if not move_evals:
        return ""

    margin_left = 45
    margin_right = 20
    margin_top = 20
    margin_bottom = 30
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    mid_y = margin_top + plot_h / 2
    max_pawns = 8.0
    n = len(move_evals)

    def x_pos(i):
        return margin_left + (i / max(n - 1, 1)) * plot_w

    def y_pos(cp):
        pawns = max(-max_pawns, min(max_pawns, cp / 100))
        return mid_y - (pawns / max_pawns) * (plot_h / 2)

    # Build eval points
    points = []
    for i, entry in enumerate(move_evals):
        if entry["mate_in"] is not None:
            cp = MATE_SCORE_CP if entry["mate_in"] > 0 else -MATE_SCORE_CP
        else:
            cp = entry["eval_cp"]
        points.append((x_pos(i), y_pos(cp), cp, entry))

    parts = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
                 f'width="{width}" height="{height}" style="max-width:100%;height:auto;">')

    # Background
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="#1a1a2e" rx="8"/>')

    # Fill regions — split the line at y=mid_y for White/Black advantage areas
    # White advantage (above zero line) — light fill
    line_coords = " ".join(f"{px},{py}" for px, py, _, _ in points)
    # Closed polygon for above-zero fill
    white_poly = f"M{margin_left},{mid_y} "
    for px, py, cp, _ in points:
        cy = min(py, mid_y)
        white_poly += f"L{px},{cy} "
    white_poly += f"L{points[-1][0]},{mid_y} Z"
    parts.append(f'<path d="{white_poly}" fill="rgba(255,255,255,0.1)" />')

    # Black advantage (below zero line) — dark fill
    black_poly = f"M{margin_left},{mid_y} "
    for px, py, cp, _ in points:
        cy = max(py, mid_y)
        black_poly += f"L{px},{cy} "
    black_poly += f"L{points[-1][0]},{mid_y} Z"
    parts.append(f'<path d="{black_poly}" fill="rgba(50,50,50,0.6)" />')

    # Zero line
    parts.append(f'<line x1="{margin_left}" y1="{mid_y}" x2="{margin_left + plot_w}" y2="{mid_y}" '
                 f'stroke="#555" stroke-width="1" stroke-dasharray="4,4"/>')

    # Eval line
    polyline = " ".join(f"{px},{py}" for px, py, _, _ in points)
    parts.append(f'<polyline points="{polyline}" fill="none" stroke="#4a9eff" stroke-width="2"/>')

    # Y-axis labels
    for label_val in [-8, -4, 0, 4, 8]:
        ly = y_pos(label_val * 100)
        parts.append(f'<text x="{margin_left - 5}" y="{ly + 4}" '
                     f'text-anchor="end" fill="#888" font-size="11">'
                     f'{label_val:+d}</text>')

    # X-axis labels (every 5 full moves)
    for i, entry in enumerate(move_evals):
        if entry["is_white"]:
            move_num = (entry["half_move"] + 1) // 2
            if move_num % 5 == 0 or move_num == 1:
                lx = x_pos(i)
                parts.append(f'<text x="{lx}" y="{height - 5}" '
                             f'text-anchor="middle" fill="#888" font-size="11">'
                             f'{move_num}</text>')

    # Critical moment markers
    for px, py, cp, entry in points:
        mt = entry.get("moment_type")
        if mt == "blunder":
            parts.append(f'<circle cx="{px}" cy="{py}" r="5" fill="#cc0000" stroke="#fff" stroke-width="1">'
                         f'<title>{html.escape(entry["san"])} ({cp/100:+.1f}) Blunder</title></circle>')
        elif mt in ("missed_chance", "missed_mate"):
            parts.append(f'<circle cx="{px}" cy="{py}" r="5" fill="#d4ac0d" stroke="#fff" stroke-width="1">'
                         f'<title>{html.escape(entry["san"])} ({cp/100:+.1f}) Missed</title></circle>')

    parts.append('</svg>')
    return "\n".join(parts)


def generate_html_report(moments: List[CrucialMoment], metadata: Dict[str, str],
                         output_dir: str = "docs/analysis",
                         move_evals: Optional[List[Dict[str, Any]]] = None,
                         lichess_url: Optional[str] = None):
    """
    Generates a self-contained HTML report with inline SVGs.

    Args:
        moments: The list of analyzed moments.
        metadata: Game metadata (White, Black, Date, etc.).
        output_dir: Directory to save the HTML report.
        move_evals: Per-half-move eval list for chart/PGN annotation.
        lichess_url: Optional Lichess game URL for deep links.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    safe_date = metadata['Date'].replace('.', '-')
    safe_white = "".join(c for c in metadata['White'] if c.isalnum() or c in (' ', '_')).replace(' ', '_')
    safe_black = "".join(c for c in metadata['Black'] if c.isalnum() or c in (' ', '_')).replace(' ', '_')
    filename = f"{safe_date}_{safe_white}_vs_{safe_black}.html"
    output_path = os.path.join(output_dir, filename)

    utc_date = metadata.get('UTCDate') or metadata.get('Date', '')
    utc_time = metadata.get('UTCTime', '00:00:00')
    report_meta = json.dumps({
        "date": metadata.get('Date', ''),
        "datetime": f"{utc_date}T{utc_time}",
        "white": metadata.get('White', ''),
        "black": metadata.get('Black', ''),
        "result": metadata.get('Result', ''),
        "moment_count": len(moments),
    })

    white_esc = html.escape(metadata.get('White', '?'))
    black_esc = html.escape(metadata.get('Black', '?'))
    date_esc = html.escape(metadata.get('Date', '?'))
    event_esc = html.escape(metadata.get('Event', ''))
    site_esc = html.escape(metadata.get('Site', ''))

    parts = []
    lichess_link_html = ""
    if lichess_url:
        lichess_link_html = (
            f'    <a href="{html.escape(lichess_url)}" target="_blank" class="lichess-btn">'
            f'<svg viewBox="0 0 50 50" width="18" height="18" style="vertical-align:middle;margin-right:6px;fill:currentColor">'
            f'<path d="M25 1C11.7 1 1 11.7 1 25s10.7 24 24 24 24-10.7 24-24S38.3 1 25 1zm0 4'
            f'c5 0 9.6 1.8 13.2 4.9L25 23.1 11.8 9.9C15.4 6.8 20 5 25 5zm-15 8.7L23.2 27'
            f' 10 40.3C6.8 36.6 5 32 5 27c0-5 1.8-9.6 5-13.3zM25 45c-5 0-9.6-1.8-13.2-5'
            f'L25 26.9l13.2 13.2C34.6 43.2 30 45 25 45zm15-8.7L26.8 23 40 9.7c3.1 3.7 5 8.3'
            f' 5 13.3 0 5-1.8 9.6-5 13.3z"/></svg>'
            f'Analyze on Lichess</a>\n'
        )

    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="theme-color" content="#1a1a1a">
    <meta name="report-data" content='{html.escape(report_meta, quote=True)}'>
    <title>Analysis: {white_esc} vs {black_esc}</title>
    <style>{_HTML_STYLE}</style>
</head>
<body>
<div class="container">
    <a href="index.html" class="back-link">&larr; Back to Index</a>
    <h1>Analysis: {white_esc} vs {black_esc}</h1>
    <div class="meta"><strong>Date:</strong> {date_esc} | <strong>Event:</strong> {event_esc} | <strong>Site:</strong> {site_esc}</div>
{lichess_link_html}""")

    # Eval chart (above moments)
    if move_evals:
        chart_svg = generate_eval_chart_svg(move_evals)
        if chart_svg:
            parts.append('    <h2 style="margin-bottom:12px">Evaluation</h2>\n')
            parts.append('    <div style="background:#2a2a3e;border-radius:8px;padding:16px;margin-bottom:24px;overflow-x:auto;">\n')
            parts.append(f'        {chart_svg}\n')
            parts.append('    </div>\n')

    if not moments:
        parts.append('    <p class="empty-msg">No crucial moments (blunders/missed wins) detected for the hero in this game.</p>\n')
    else:
        blunder_count = sum(1 for m in moments if m.moment_type == "blunder")
        missed_count = sum(1 for m in moments if m.moment_type in ("missed_chance", "missed_mate"))
        summary_text = f'Found <strong>{len(moments)}</strong> crucial moment{"s" if len(moments) != 1 else ""}'
        if missed_count:
            summary_text += f' ({blunder_count} blunder{"s" if blunder_count != 1 else ""}, {missed_count} missed opportunity{"s" if missed_count != 1 else ""})'
        parts.append(f'    <p class="summary-count">{summary_text}.</p>\n')

        for i, moment in enumerate(moments, 1):
            type_label = html.escape(MOMENT_TYPE_LABELS.get(moment.moment_type, "Blunder"))
            severity_label = moment.severity.upper()
            severity_color = SEVERITY_COLORS.get(moment.severity, "#7f8c8d")
            tactic_label = TACTIC_LABELS.get(moment.tactic_type, moment.tactic_type)
            tactic_color = TACTIC_COLORS.get(moment.tactic_type, "#7f8c8d")
            is_missed = moment.moment_type in ("missed_chance", "missed_mate")
            if is_missed:
                tactic_label = f"Missed {tactic_label}"

            # Per-moment deep link to Lichess position
            moment_link = ""
            if lichess_url and moment.half_move_number is not None:
                deep_url = f"{lichess_url}#{moment.half_move_number}"
                moment_link = f' <a href="{html.escape(deep_url)}" target="_blank" class="moment-lichess-link" title="View on Lichess">&#x2197;</a>'

            parts.append(f'    <div class="moment-card">\n')
            parts.append(f'        <h2>Moment {i} <span class="moment-type-label">— {type_label}</span>'
                         f' <span class="severity-pill" style="background:{severity_color}">{severity_label}</span>'
                         f'{moment_link}</h2>\n')
            parts.append(f'        <span class="tactic-badge" style="background:{tactic_color}">{html.escape(tactic_label)}</span>\n')

            if moment.svg_content:
                parts.append(f'        <div class="board-svg">{moment.svg_content}</div>\n')

            parts.append(f'        <div class="fen">FEN: {html.escape(moment.fen)}</div>\n')

            parts.append('        <div class="move-info">\n')
            if is_missed:
                parts.append(f'            <span class="move-played">You Played: {html.escape(moment.move_played_san)}</span><br>\n')
                parts.append(f'            <span class="move-best">You Could Have Played: {html.escape(moment.best_move_san)}</span><br>\n')
            else:
                parts.append(f'            <span class="move-played">You Played: {html.escape(moment.move_played_san)}</span><br>\n')
                parts.append(f'            <span class="move-best">Engine Best: {html.escape(moment.best_move_san)}</span><br>\n')
            parts.append(f'            <span class="eval-swing">Eval Swing: {moment.eval_swing} cp</span><br>\n')
            parts.append(f'            <span class="variation">Variation: {html.escape(moment.pv_line)}</span>\n')
            parts.append('        </div>\n')

            if moment.tactical_alert:
                parts.append(f'        <div class="tactical-alert">{html.escape(moment.tactical_alert)}</div>\n')

            if is_missed and moment.best_line:
                player_html = generate_line_player_html(
                    player_id=f"player-{i}",
                    start_fen=moment.fen,
                    line_san=moment.best_line,
                    hero_color=moment.hero_color,
                    initial_move_san=None,
                    kind="best-line",
                    start_label="Before the move",
                )
                if player_html:
                    parts.append(f'        {player_html}\n')

            if moment.refutation_line and not is_missed:
                player_html = generate_line_player_html(
                    player_id=f"player-{i}",
                    start_fen=moment.fen,
                    line_san=moment.refutation_line,
                    hero_color=moment.hero_color,
                    initial_move_san=moment.move_played_san,
                    kind="refutation",
                    start_label="After your move",
                )
                if player_html:
                    parts.append(f'        {player_html}\n')

            parts.append('    </div>\n')

    # Annotated PGN
    if move_evals:
        pgn_string = generate_annotated_pgn(metadata, move_evals, moments)
        if pgn_string:
            pgn_escaped = html.escape(pgn_string)
            parts.append('    <h2 style="margin-top:24px;margin-bottom:12px">Annotated PGN</h2>\n')
            parts.append('    <p style="font-size:0.85rem;color:#666;margin-bottom:8px">'
                         'Copy this PGN to paste into Lichess, ChessBase, or any analysis tool.</p>\n')
            parts.append("""    <div style="
        background:#1a1a2e;color:#e0e0e0;padding:16px 20px;border-radius:8px;
        font-family:'SF Mono','Fira Code','Consolas',monospace;font-size:0.8rem;
        line-height:1.6;white-space:pre-wrap;word-break:break-word;position:relative;
        max-height:400px;overflow-y:auto;">
        <button onclick="navigator.clipboard.writeText(this.nextElementSibling.textContent)"
            style="position:absolute;top:8px;right:8px;background:#333;color:#ccc;
            border:1px solid #555;padding:4px 12px;border-radius:4px;cursor:pointer;
            font-size:0.75rem;">Copy</button>
""")
            parts.append(f'        <code id="annotated-pgn">{pgn_escaped}</code>\n')
            parts.append('    </div>\n')

    parts.append("""</div>
<script>
(function() {
  document.querySelectorAll('.line-player').forEach(function(player) {
    var frames = player.querySelectorAll('.lp-frame');
    var stage = player.querySelector('.lp-stage');
    var caption = player.querySelector('.lp-caption');
    var counter = player.querySelector('.lp-counter');
    var prev = player.querySelector('.lp-prev');
    var next = player.querySelector('.lp-next');
    var play = player.querySelector('.lp-play');
    var idx = 0, playing = false, timer = null;
    function render() {
      stage.innerHTML = frames[idx].innerHTML;
      caption.textContent = frames[idx].dataset.caption;
      counter.textContent = idx + '/' + (frames.length - 1);
      prev.disabled = (idx === 0);
      next.disabled = (idx === frames.length - 1);
    }
    function step(d) {
      idx = Math.max(0, Math.min(frames.length - 1, idx + d));
      render();
    }
    function toggle() {
      if (playing) {
        clearInterval(timer); playing = false;
        play.innerHTML = '&#9654;';
      } else {
        if (idx === frames.length - 1) idx = 0;
        playing = true;
        play.innerHTML = '&#9208;';
        timer = setInterval(function() {
          if (idx >= frames.length - 1) {
            clearInterval(timer); playing = false;
            play.innerHTML = '&#9654;';
            return;
          }
          step(1);
        }, 900);
      }
    }
    prev.addEventListener('click', function() { step(-1); });
    next.addEventListener('click', function() { step(1); });
    play.addEventListener('click', toggle);
    render();
  });
})();
</script>
</body>
</html>
""")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))

    logger.info(f"HTML report generated: {output_path}")


def regenerate_index_page(html_output_dir: str):
    """
    Scans html_output_dir for report HTML files and generates an index.html listing them.

    Args:
        html_output_dir: Directory containing the HTML report files.
    """
    if not os.path.exists(html_output_dir):
        os.makedirs(html_output_dir)

    reports = []
    for fname in os.listdir(html_output_dir):
        if not fname.endswith('.html') or fname == 'index.html':
            continue
        fpath = os.path.join(html_output_dir, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read(4096)  # metadata is near the top
            match = re.search(r'<meta\s+name="report-data"\s+content=\'([^\']+)\'', content)
            if match:
                meta = json.loads(html.unescape(match.group(1)))
                meta['filename'] = fname
                reports.append(meta)
        except Exception as e:
            logger.warning(f"Could not read metadata from {fname}: {e}")

    # Sort by datetime descending (fall back to date for old reports without UTCTime)
    reports.sort(
        key=lambda r: r.get('datetime') or r.get('date', ''),
        reverse=True
    )

    parts = []
    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="theme-color" content="#1a1a1a">
    <title>Analysis Reports</title>
    <style>{_HTML_STYLE}</style>
</head>
<body>
<div class="container">
    <h1>Analysis Reports</h1>
    <div class="meta">Game analysis with Stockfish engine.</div>
    <div class="run-bar">
        <button class="run-btn" id="runBtn" onclick="triggerAnalysis()">&#9654; Run Analysis</button>
        <a class="drills-btn" href="../drills/index.html">&#127919; Drills</a>
        <span class="run-status" id="runStatus"></span>
        <span class="token-link" id="tokenLink" onclick="resetToken()" title="Update stored GitHub token" style="display:none">[change token]</span>
    </div>
    <script>
    (function() {{
        var tokenLink = document.getElementById('tokenLink');
        if (localStorage.getItem('gh_actions_pat')) tokenLink.style.display = '';
    }})();

    function triggerAnalysis() {{
        var pat = localStorage.getItem('gh_actions_pat');
        if (!pat) {{
            pat = prompt('Enter a GitHub fine-grained PAT with Actions: write scope for e-riveras/chess-tools:');
            if (!pat) return;
            localStorage.setItem('gh_actions_pat', pat.trim());
            document.getElementById('tokenLink').style.display = '';
        }}
        var btn = document.getElementById('runBtn');
        var status = document.getElementById('runStatus');
        btn.disabled = true;
        btn.textContent = '⏳ Triggering…';
        status.className = 'run-status';
        status.textContent = '';
        fetch('https://api.github.com/repos/e-riveras/chess-tools/actions/workflows/sync.yml/dispatches', {{
            method: 'POST',
            headers: {{
                'Authorization': 'Bearer ' + localStorage.getItem('gh_actions_pat'),
                'Accept': 'application/vnd.github+json',
                'Content-Type': 'application/json'
            }},
            body: JSON.stringify({{ ref: 'main' }})
        }}).then(function(r) {{
            btn.disabled = false;
            btn.innerHTML = '&#9654; Run Analysis';
            if (r.status === 204) {{
                status.className = 'run-status ok';
                status.textContent = '✓ Workflow triggered — check Actions tab for progress.';
            }} else if (r.status === 401 || r.status === 403) {{
                status.className = 'run-status err';
                status.textContent = '✗ Auth failed. Token may be invalid or lack Actions: write scope.';
            }} else {{
                status.className = 'run-status err';
                status.textContent = '✗ Error ' + r.status + '. Check token permissions.';
            }}
        }}).catch(function(e) {{
            btn.disabled = false;
            btn.innerHTML = '&#9654; Run Analysis';
            status.className = 'run-status err';
            status.textContent = '✗ Network error: ' + e.message;
        }});
    }}

    function resetToken() {{
        localStorage.removeItem('gh_actions_pat');
        document.getElementById('tokenLink').style.display = 'none';
        document.getElementById('runStatus').textContent = '';
        alert('Token cleared. You will be prompted on next run.');
    }}
    </script>
""")

    if not reports:
        parts.append('    <p class="no-reports">No analysis reports yet.</p>\n')
    else:
        parts.append('    <div class="report-grid">\n')
        for r in reports:
            white_esc = html.escape(r.get('white', '?'))
            black_esc = html.escape(r.get('black', '?'))
            date_esc = html.escape(r.get('date', '?'))
            result_esc = html.escape(r.get('result', ''))
            count = r.get('moment_count', 0)
            fname_esc = html.escape(r['filename'])

            parts.append(f"""        <a href="{fname_esc}" class="report-card">
            <h3>{white_esc} vs {black_esc}</h3>
            <div class="card-meta">{date_esc} &middot; {result_esc}</div>
            <div class="card-moments">{count} crucial moment{"s" if count != 1 else ""}</div>
        </a>
""")
        parts.append('    </div>\n')

    parts.append("""</div>
</body>
</html>
""")

    index_path = os.path.join(html_output_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))

    logger.info(f"Index page generated: {index_path} ({len(reports)} reports)")
