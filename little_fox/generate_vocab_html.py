#!/usr/bin/env python3
"""Extract Cinderella episodes from a DOCX and build an offline vocabulary page.

The script only uses Python's standard library.  The generated HTML uses the
browser's Web Speech API for English pronunciation, so no audio files or
network connection are required.
"""

from __future__ import annotations

import argparse
import html
import re
import zipfile
from collections import OrderedDict
from pathlib import Path
from xml.etree import ElementTree as ET


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TITLE_RE = re.compile(r"^Cinderella\s+(\d+)\s*:\s*(.+)$", re.IGNORECASE)
TOKEN_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")

# A learning-oriented glossary.  Only words that really occur in an episode
# are output.  Add or amend entries here to customize the resulting list.
GLOSSARY = OrderedDict(
    [
        ("once upon a time", "从前；很久以前"),
        ("kind", "善良的；友好的"),
        ("happily", "快乐地；幸福地"),
        ("fine", "精美的；优质的"),
        ("jewelry", "珠宝；首饰"),
        ("cozy", "舒适温暖的"),
        ("plenty", "充足；大量"),
        ("bakery", "面包店"),
        ("treat", "特别的美食；款待"),
        ("beggar", "乞丐"),
        ("square", "广场"),
        ("poor", "贫穷的；可怜的"),
        ("warmly", "热情地；温暖地"),
        ("generous", "慷慨的；大方的"),
        ("marry", "结婚；娶；嫁"),
        ("stepmother", "继母"),
        ("stepsister", "继姐妹"),
        ("hunting trip", "狩猎旅行"),
        ("lazy", "懒惰的"),
        ("snap", "厉声说；呵斥"),
        ("gasp", "倒吸一口气；喘气"),
        ("tear", "眼泪"),
        ("pain", "痛苦；疼痛"),
        ("polite", "有礼貌的"),
        ("mean", "刻薄的；吝啬的"),
        ("fancy", "华丽的；精致的"),
        ("rag", "破布；破旧衣服"),
        ("ash", "灰；灰烬"),
        ("fireplace", "壁炉"),
        ("chore", "日常杂务；家务"),
        ("royal", "王室的；皇家的"),
        ("messenger", "信使；送信人"),
        ("trumpet", "小号；喇叭"),
        ("apron", "围裙"),
        ("crowd", "人群"),
        ("announcement", "公告；宣布"),
        ("host", "举办；主持"),
        ("kingdom", "王国"),
        ("invite", "邀请"),
        ("bow", "鞠躬"),
        ("curtsy", "屈膝礼"),
        ("ball", "舞会"),
        ("gown", "长礼服；礼服"),
        ("jewel", "宝石"),
        ("wicked", "邪恶的；恶毒的"),
        ("grin", "咧嘴笑"),
        ("delight", "高兴；喜悦"),
        ("impress", "给……留下深刻印象"),
        ("palace", "宫殿"),
        ("servant", "仆人；佣人"),
        ("visitor", "访客；来宾"),
        ("awful", "糟糕的；难受的"),
        ("alone", "独自；单独"),
        ("wish", "愿望；希望"),
        ("bucket", "水桶"),
        ("scrub", "用力擦洗"),
        ("fountain", "喷泉"),
        ("sparkling", "闪闪发光的"),
        ("wing", "翅膀"),
        ("fairy", "仙女；小精灵"),
        ("godmother", "教母"),
        ("appear", "出现"),
        ("magic", "魔法；有魔力的"),
        ("wand", "魔杖"),
        ("pumpkin", "南瓜"),
        ("coach", "四轮马车"),
        ("golden", "金色的；金制的"),
        ("mouse", "老鼠"),
        ("rat", "大老鼠"),
        ("cellar", "地窖；地下室"),
        ("jolly", "快活的；愉快的"),
        ("driver", "车夫；司机"),
        ("whisker", "胡须"),
        ("splendid", "极好的；华丽的"),
        ("midnight", "午夜"),
        ("obey", "服从；遵守"),
        ("rule", "规则；规定"),
        ("disappear", "消失"),
        ("promise", "答应；承诺"),
        ("toe", "脚趾"),
        ("boring", "无聊的"),
        ("princess", "公主"),
        ("serve", "端上；服务"),
        ("tray", "托盘"),
        ("spot", "发现；认出"),
        ("giggle", "咯咯地笑"),
        ("quietly", "安静地；轻声地"),
        ("realize", "意识到"),
        ("sadly", "伤心地；遗憾地"),
        ("sing softly", "轻声歌唱"),
        ("bark", "厉声说；吼叫"),
        ("order", "命令；吩咐"),
        ("bother", "打扰；使烦恼"),
        ("lovely", "可爱的；美好的"),
        ("hum", "哼唱"),
        ("sweep", "打扫；扫"),
        ("headache", "头痛"),
        ("stomp", "跺脚；重步走"),
        ("midnight", "午夜"),
        ("strike", "（钟）敲响；击打"),
        ("shout", "喊叫"),
        ("glass", "玻璃；玻璃制的"),
        ("shoe", "鞋"),
        ("drop", "掉落；使落下"),
        ("sink", "下沉；（心情）低落"),
        ("path", "小路；路径"),
        ("slowly", "慢慢地"),
        ("fit", "合身；适合"),
        ("brush away", "拂去；擦去"),
        ("try on", "试穿"),
        ("push", "推；挤"),
        ("foot", "脚"),
        ("search", "寻找；搜寻"),
        ("impossible", "不可能的"),
        ("reach into", "把手伸进……"),
        ("pocket", "口袋"),
        ("wave", "挥动；挥手"),
        ("handsome", "英俊的"),
        ("ever after", "从此以后"),
    ]
)


def read_paragraphs(docx_path: Path) -> list[str]:
    """Read paragraph text directly from the XML inside a DOCX file."""
    if not docx_path.is_file():
        raise FileNotFoundError(f"找不到 Word 文件：{docx_path}")
    try:
        with zipfile.ZipFile(docx_path) as archive:
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"{docx_path} 不是有效的 DOCX 文件") from exc

    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.findall(f".//{{{WORD_NS}}}body/{{{WORD_NS}}}p"):
        text = "".join(
            node.text or "" for node in paragraph.iter(f"{{{WORD_NS}}}t")
        )
        text = re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def split_episodes(paragraphs: list[str]) -> list[dict[str, object]]:
    episodes: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for paragraph in paragraphs:
        match = TITLE_RE.match(paragraph)
        if match:
            current = {
                "number": int(match.group(1)),
                "title": paragraph,
                "subtitle": match.group(2).strip(),
                "paragraphs": [],
            }
            episodes.append(current)
        elif current is not None:
            current["paragraphs"].append(paragraph)  # type: ignore[union-attr]
    if not episodes:
        raise ValueError("没有识别到形如“Cinderella 1: 标题”的章节标题")
    return episodes


def word_candidates(token: str) -> set[str]:
    """Return a few conservative base-form candidates for an English token."""
    word = token.lower().replace("’", "'").strip("'")
    forms = {word}
    if word.endswith("ies") and len(word) > 4:
        forms.add(word[:-3] + "y")
    if word.endswith("ied") and len(word) > 4:
        forms.add(word[:-3] + "y")
    if word.endswith("ing") and len(word) > 5:
        base = word[:-3]
        forms.update((base, base + "e"))
        if len(base) > 2 and base[-1] == base[-2]:
            forms.add(base[:-1])
    if word.endswith("ed") and len(word) > 4:
        base = word[:-2]
        forms.update((base, base + "e"))
        if len(base) > 2 and base[-1] == base[-2]:
            forms.add(base[:-1])
    if word.endswith("es") and len(word) > 4:
        forms.update((word[:-2], word[:-1]))
    if word.endswith("s") and len(word) > 3:
        forms.add(word[:-1])
    irregular = {
        "mice": "mouse",
        "feet": "foot",
        "sank": "sink",
        "sang": "sing",
        "swept": "sweep",
        "struck": "strike",
        "wore": "wear",
        "blew": "blow",
        "took": "take",
    }
    if word in irregular:
        forms.add(irregular[word])
    return forms


def term_position(term: str, paragraph: str) -> int | None:
    """Return the token position of a word/phrase, allowing common inflections."""
    words = TOKEN_RE.findall(paragraph)
    wanted = term.split()
    if len(words) < len(wanted):
        return None
    for start in range(len(words) - len(wanted) + 1):
        if all(
            base in word_candidates(words[start + offset])
            for offset, base in enumerate(wanted)
        ):
            return start
    return None


def episode_vocabulary(paragraphs: list[str]) -> list[tuple[str, str, str]]:
    """Return term, Chinese meaning and its first source sentence."""
    found: dict[str, tuple[int, int, str]] = {}
    glossary_order = {term: index for index, term in enumerate(GLOSSARY)}
    for paragraph_index, paragraph in enumerate(paragraphs):
        for term in GLOSSARY:
            if term in found:
                continue
            position = term_position(term, paragraph)
            if position is not None:
                found[term] = (paragraph_index, position, paragraph)

    ordered = sorted(
        found,
        key=lambda term: (found[term][0], found[term][1], glossary_order[term]),
    )
    return [(term, GLOSSARY[term], found[term][2]) for term in ordered]


def render_episode(episode: dict[str, object]) -> str:
    title = str(episode["title"])
    number = int(episode["number"])
    vocab = episode_vocabulary(episode["paragraphs"])  # type: ignore[arg-type]
    rows = []
    for word, meaning, example in vocab:
        search_text = f"{word} {meaning} {example}".lower()
        rows.append(
            "<tr class=\"word-row\" "
            f"data-search=\"{html.escape(search_text, quote=True)}\">"
            f"<td class=\"word\">{html.escape(word)}</td>"
            f"<td>{html.escape(meaning)}</td>"
            f"<td class=\"example\">{html.escape(example)}</td>"
            "<td><button class=\"speak\" type=\"button\" "
            f"data-word=\"{html.escape(word, quote=True)}\" "
            f"aria-label=\"朗读 {html.escape(word, quote=True)}\">"
            "<span aria-hidden=\"true\">🔊</span><span>发音</span></button></td></tr>"
        )
    return f"""
    <section class="episode" id="episode-{number}" data-episode="{number}">
      <div class="episode-title">
        <span class="episode-number">EP {number:02d}</span>
        <h2>{html.escape(title)}</h2>
        <span class="count">{len(vocab)} 个词</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>单词 / 短语</th><th>中文解释</th><th>英文范例</th><th>发音</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
        <p class="empty">本章节没有匹配到词汇。</p>
      </div>
    </section>"""


def build_html(episodes: list[dict[str, object]], source_name: str) -> str:
    sections = "".join(render_episode(episode) for episode in episodes)
    total = sum(len(episode_vocabulary(e["paragraphs"])) for e in episodes)  # type: ignore[arg-type]
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
    .word {{ width:19%; color:#63283f; font-family:Georgia,serif; font-size:1.08rem; font-weight:700; }}
    .example {{ width:42%; color:#51495a; font-family:Georgia,serif; font-size:.95rem; }}
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
      table {{ min-width:760px; }} th,td {{ padding:13px 16px; }}
      th:nth-child(4),td:nth-child(4) {{ text-align:right; }}
      .speak span:last-child {{ display:none; }}
    }}
  </style>
</head>
<body>
  <header class="hero">
    <div class="eyebrow">LITTLE FOX · WORD BOOK</div>
    <h1>Cinderella</h1>
    <p class="lead">按故事章节整理的英语词汇，英文范例取自原文。点击每个单词旁的按钮，即可听到英文发音。</p>
    <div class="stats">{len(episodes)} EPISODES · {total} 条章节词汇</div>
  </header>
  <div class="toolbar">
    <input id="search" class="search" type="search" placeholder="搜索英文或中文……" aria-label="搜索词汇">
    <nav aria-label="章节导航">{nav}</nav>
  </div>
  <main>{sections}</main>
  <footer>内容提取自 {html.escape(source_name)} · 发音由浏览器语音功能提供</footer>
  <script>
    const synth = window.speechSynthesis;
    let activeButton = null;
    function englishVoice() {{
      const voices = synth ? synth.getVoices() : [];
      return voices.find(v => /^en-(US|GB)/i.test(v.lang)) || voices.find(v => /^en/i.test(v.lang));
    }}
    document.addEventListener('click', event => {{
      const button = event.target.closest('.speak');
      if (!button) return;
      if (!synth) {{ alert('当前浏览器不支持语音朗读，请使用新版 Chrome、Edge 或 Safari。'); return; }}
      synth.cancel();
      if (activeButton) activeButton.classList.remove('playing');
      const utterance = new SpeechSynthesisUtterance(button.dataset.word);
      utterance.lang = 'en-US'; utterance.rate = 0.82;
      const voice = englishVoice(); if (voice) utterance.voice = voice;
      button.classList.add('playing'); activeButton = button;
      utterance.onend = utterance.onerror = () => button.classList.remove('playing');
      synth.speak(utterance);
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
    parser = argparse.ArgumentParser(description="从 Cinderella.docx 生成章节词汇 HTML")
    parser.add_argument("input", nargs="?", type=Path, default=script_dir / "Cinderella.docx")
    parser.add_argument("-o", "--output", type=Path, default=script_dir / "cinderella_vocabulary.html")
    args = parser.parse_args()

    episodes = split_episodes(read_paragraphs(args.input))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(episodes, args.input.name), encoding="utf-8")
    total = sum(len(episode_vocabulary(e["paragraphs"])) for e in episodes)  # type: ignore[arg-type]
    print(f"已生成：{output}")
    print(f"识别到 {len(episodes)} 个章节，共输出 {total} 条章节词汇。")


if __name__ == "__main__":
    main()
