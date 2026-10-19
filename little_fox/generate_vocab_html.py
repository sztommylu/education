#!/usr/bin/env python3
"""Build Cinderella's offline vocabulary page from the numbered PDF glossary."""

from __future__ import annotations

import argparse
import hashlib
import html
import re
from pathlib import Path

from pypdf import PdfReader


TITLE_RE = re.compile(r"Cinderella\s+(\d+)\s*:\s*(.+)", re.IGNORECASE)
ROW_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)(?: {2,}(.+))?$")
CONTINUED_WORD_RE = re.compile(r"^ {4,18}(\S.+?)(?: {2,}(.+))?$")
CHINESE_RE = re.compile(r"[\u2e80-\u9fff]")


def clean(value: str) -> str:
    """Normalize PDF tabs and spacing without changing source words."""
    return re.sub(r"\s+", " ", value).strip()


def put_cell(row: dict[str, object], value: str) -> None:
    """Keep a PDF meaning and its example in separate HTML columns."""
    value = clean(value)
    field = "meaning" if CHINESE_RE.search(value) else "phrase"
    row[field] = (str(row[field]) + " " + value).strip()


def audio_filename(word: str) -> str:
    """Give repeated PDF terms the same safe, stable audio filename."""
    return hashlib.sha256(word.encode("utf-8")).hexdigest()[:16] + ".mp3"


def read_episodes(pdf_path: Path) -> list[dict[str, object]]:
    """Read numbered entries, meanings and examples in their PDF order."""
    if not pdf_path.is_file():
        raise FileNotFoundError(f"找不到 PDF 文件：{pdf_path}")
    episodes: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    row: dict[str, object] | None = None
    for page_number, page in enumerate(PdfReader(str(pdf_path)).pages, start=1):
        lines = page.extract_text(extraction_mode="layout").splitlines()
        for line in lines:
            stripped = line.strip()
            if not stripped or re.fullmatch(r"\d+\s*/\s*\d+", stripped):
                continue
            title_match = TITLE_RE.fullmatch(clean(stripped))
            if title_match:
                current = {
                    "number": int(title_match.group(1)),
                    "title": clean(stripped),
                    "rows": [],
                }
                episodes.append(current)
                row = None
                continue
            match = ROW_RE.match(line)
            if match:
                if current is None:
                    raise ValueError(f"PDF 第 {page_number} 页在章节标题前出现词条")
                number = int(match.group(1))
                expected = len(current["rows"]) + 1
                if number != expected:
                    raise ValueError(f"PDF 第 {page_number} 页词条序号为 {number}，预期 {expected}")
                row = {"number": number, "word": clean(match.group(2)), "meaning": "", "phrase": ""}
                current["rows"].append(row)
                if match.group(3):
                    put_cell(row, match.group(3))
                continue
            if row is None:
                raise ValueError(f"PDF 第 {page_number} 页有无法识别的内容：{stripped}")
            word_match = CONTINUED_WORD_RE.match(line)
            if word_match:
                row["word"] += " " + clean(word_match.group(1))
                if word_match.group(2):
                    put_cell(row, word_match.group(2))
            elif len(line) - len(line.lstrip(" ")) >= 19:
                put_cell(row, stripped)
            else:
                raise ValueError(f"PDF 第 {page_number} 页有无法识别的续行：{stripped}")
    if not episodes:
        raise ValueError("PDF 中没有识别到 Cinderella 章节")
    return episodes

def render_episode(episode: dict[str, object]) -> str:
    title = str(episode["title"])
    number = int(episode["number"])
    vocab = episode["rows"]
    rows = []
    for entry in vocab:
        index = entry["number"]
        word = str(entry["word"])
        meaning = str(entry["meaning"])
        phrase = str(entry["phrase"])
        search_text = f"{word} {meaning} {phrase}".lower()
        rows.append(
            "<tr class=\"word-row\" "
            f"data-search=\"{html.escape(search_text, quote=True)}\">"
            f"<td class=\"number\">{index}</td>"
            f"<td class=\"word\">{html.escape(word)}</td>"
            f"<td class=\"meaning\">{html.escape(meaning)}</td>"
            f"<td class=\"phrase\">{html.escape(phrase)}</td>"
            "<td><button class=\"speak\" type=\"button\" "
            f"data-word=\"{html.escape(word, quote=True)}\" "
            f"data-audio=\"audio/{audio_filename(word)}\" "
            f"aria-label=\"朗读 {html.escape(word, quote=True)}\">"
            "<span aria-hidden=\"true\">🔊</span><span>发音</span></button></td></tr>"
        )
    return f"""
    <section class="episode" id="episode-{number}" data-episode="{number}">
      <div class="episode-title">
        <span class="episode-number">EP {number:02d}</span>
        <h2>{html.escape(title)}</h2>
        <span class="count">{len(vocab)} 条词汇</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>序号</th><th>单词</th><th>中文</th><th>短语</th><th>发音</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
        <p class="empty">本章节没有匹配到词汇。</p>
      </div>
    </section>"""


def build_html(episodes: list[dict[str, object]], source_name: str) -> str:
    sections = "".join(render_episode(episode) for episode in episodes)
    total = sum(len(e["rows"]) for e in episodes)
    nav = "".join(
        f'<a href="#episode-{e["number"]}">{int(e["number"]):02d}</a>'
        for e in episodes
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cinderella · Episode Vocabulary</title>
  <style>
    :root {{ --ink:#292337; --muted:#746d80; --paper:#fffaf2; --card:#fff;
      --wine:#8d3152; --gold:#d9a441; --line:#eadfce; --shadow:0 14px 40px #49352416; }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); background:
      radial-gradient(circle at 15% 0%, #f8dfd8 0 11rem, transparent 26rem),
      linear-gradient(180deg,#fffaf2,#f7efe5); font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif; }}
    .hero {{ padding:70px 24px 38px; text-align:center; }}
    .eyebrow {{ color:var(--wine); font-size:.78rem; font-weight:800; letter-spacing:.22em; }}
    h1 {{ margin:.35rem 0 .55rem; font:700 clamp(2.6rem,7vw,5.2rem)/1 Georgia,serif; color:#4a2539; }}
    .lead {{ margin:auto; max-width:680px; color:var(--muted); line-height:1.75; }}
    .stats {{ margin-top:18px; color:#8d3152; font-size:.9rem; font-weight:700; }}
    .toolbar {{ position:sticky; top:0; z-index:5; display:flex; gap:14px; align-items:center;
      max-width:1060px; margin:0 auto 28px; padding:12px 18px; border:1px solid #ffffffaa;
      border-radius:18px; background:#fffaf2e8; box-shadow:0 8px 30px #513a2412; backdrop-filter:blur(10px); }}
    .search {{ min-width:180px; flex:1; padding:11px 15px; border:1px solid var(--line); border-radius:11px;
      background:white; color:var(--ink); font:inherit; outline:none; }}
    .search:focus {{ border-color:var(--gold); box-shadow:0 0 0 3px #d9a44122; }}
    nav {{ display:flex; gap:5px; overflow-x:auto; scrollbar-width:thin; }}
    nav a {{ flex:0 0 auto; padding:8px; border-radius:8px; color:var(--wine); text-decoration:none; font-size:.78rem; font-weight:800; }}
    nav a:hover {{ background:#f7e6df; }}
    main {{ max-width:1060px; margin:auto; padding:0 18px 80px; }}
    .episode {{ scroll-margin-top:88px; margin:0 0 28px; overflow:hidden; border:1px solid var(--line);
      border-radius:22px; background:var(--card); box-shadow:var(--shadow); }}
    .episode-title {{ display:grid; grid-template-columns:auto 1fr auto; gap:16px; align-items:center;
      padding:21px 24px; background:linear-gradient(105deg,#fff8ec,#fceeed); border-bottom:1px solid var(--line); }}
    .episode-title h2 {{ margin:0; font:700 clamp(1.1rem,2.5vw,1.55rem)/1.25 Georgia,"Microsoft YaHei",serif; }}
    .episode-number {{ padding:7px 9px; border-radius:8px; background:var(--wine); color:white; font-size:.72rem; font-weight:800; letter-spacing:.08em; }}
    .count {{ color:var(--muted); font-size:.78rem; }}
    .table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; }}
    th,td {{ padding:15px 24px; text-align:left; border-bottom:1px solid #f0e8dc; }}
    th {{ color:var(--muted); background:#fffcf7; font-size:.78rem; letter-spacing:.05em; }}
    td {{ line-height:1.5; }}
    tr:last-child td {{ border-bottom:0; }}
    tbody tr:hover {{ background:#fffbf4; }}
    .number {{ width:7%; color:var(--muted); font-size:.85rem; font-weight:700; }}
    .word {{ width:22%; color:#63283f; font-family:Georgia,serif; font-size:1.08rem; font-weight:700; }}
    .meaning {{ width:25%; }}
    .phrase {{ width:34%; color:#51495a; font-family:Georgia,serif; font-size:.95rem; }}
    .speak {{ display:inline-flex; gap:7px; align-items:center; padding:8px 11px; border:1px solid #ead8be;
      border-radius:999px; color:#6d3b24; background:#fff7e7; cursor:pointer; font:600 .82rem inherit; white-space:nowrap; }}
    .speak:hover {{ border-color:var(--gold); background:#ffefce; }}
    .speak.playing {{ color:white; background:var(--wine); border-color:var(--wine); }}
    .empty {{ display:none; margin:0; padding:28px; text-align:center; color:var(--muted); }}
    .episode.no-results .empty {{ display:block; }}
    .episode.no-results table {{ display:none; }}
    .episode.hidden {{ display:none; }}
    footer {{ padding:0 20px 42px; color:var(--muted); text-align:center; font-size:.8rem; }}
    @media (max-width:680px) {{
      .hero {{ padding-top:45px; }} .toolbar {{ margin-inline:12px; flex-direction:column; align-items:stretch; }}
      .episode-title {{ grid-template-columns:auto 1fr; padding:17px; }} .count {{ display:none; }}
      table {{ min-width:850px; }} th,td {{ padding:13px 16px; }}
      th:nth-child(5),td:nth-child(5) {{ text-align:right; }}
      .speak span:last-child {{ display:none; }}
    }}
  </style>
</head>
<body>
  <header class="hero">
    <div class="eyebrow">LITTLE FOX · WORD BOOK</div>
    <h1>Cinderella</h1>
    <p class="lead">按 PDF 原有章节和序号整理词汇，短语栏保留 PDF 中的原文例句。点击发音按钮可听英文发音。</p>
    <div class="stats">{len(episodes)} EPISODES · {total} 条章节词汇</div>
  </header>
  <div class="toolbar">
    <input id="search" class="search" type="search" placeholder="搜索英文或中文……" aria-label="搜索词汇">
    <nav aria-label="章节导航">{nav}</nav>
  </div>
  <main>{sections}</main>
  <footer>内容提取自 {html.escape(source_name)} · 发音音频随页面提供</footer>
  <script>
    const synth = window.speechSynthesis;
    const player = new Audio();
    let activeButton = null;
    let fallbackStarted = false;
    function englishVoice() {{
      const voices = synth ? synth.getVoices() : [];
      return voices.find(v => /^en-(US|GB)/i.test(v.lang)) || voices.find(v => /^en/i.test(v.lang));
    }}
    function clearPlaying() {{
      if (activeButton) activeButton.classList.remove('playing');
      activeButton = null;
    }}
    function speakWithBrowser(button) {{
      if (fallbackStarted) return;
      fallbackStarted = true;
      if (!synth || !window.SpeechSynthesisUtterance) {{
        clearPlaying();
        alert('音频无法播放，当前浏览器也不支持语音朗读。');
        return;
      }}
      const utterance = new SpeechSynthesisUtterance(button.dataset.word);
      utterance.lang = 'en-US'; utterance.rate = 0.82;
      const voice = englishVoice(); if (voice) utterance.voice = voice;
      utterance.onend = clearPlaying;
      utterance.onerror = () => {{
        clearPlaying();
        alert('发音播放失败，请检查浏览器音量和音频输出设备。');
      }};
      synth.speak(utterance);
    }}
    player.addEventListener('ended', clearPlaying);
    player.addEventListener('error', () => {{
      if (activeButton) speakWithBrowser(activeButton);
    }});
    document.addEventListener('click', event => {{
      const button = event.target.closest('.speak');
      if (!button) return;
      player.pause();
      if (synth) synth.cancel();
      clearPlaying();
      fallbackStarted = false;
      activeButton = button;
      button.classList.add('playing');
      player.src = button.dataset.audio;
      player.play().catch(() => {{
        if (activeButton === button) speakWithBrowser(button);
      }});
    }});
    document.querySelector('#search').addEventListener('input', event => {{
      const query = event.target.value.trim().toLowerCase();
      document.querySelectorAll('.episode').forEach(section => {{
        let matches = 0;
        section.querySelectorAll('.word-row').forEach(row => {{
          const show = !query || row.dataset.search.includes(query);
          row.hidden = !show; if (show) matches++;
        }});
        section.classList.toggle('hidden', query && matches === 0);
        section.classList.toggle('no-results', !query && matches === 0);
      }});
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="从 02.Cinderella-words.pdf 生成章节词汇 HTML")
    parser.add_argument("input", nargs="?", type=Path, default=script_dir / "02.Cinderella-words.pdf")
    parser.add_argument("-o", "--output", type=Path, default=script_dir / "cinderella_vocabulary.html")
    args = parser.parse_args()

    episodes = read_episodes(args.input)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(episodes, args.input.name), encoding="utf-8")
    total = sum(len(e["rows"]) for e in episodes)
    print(f"已生成：{output}")
    print(f"识别到 {len(episodes)} 个章节，共输出 {total} 条章节词汇。")


if __name__ == "__main__":
    main()
