#!/usr/bin/env python3
"""Generate natural sounding MP3 pronunciation files for PDF vocabulary.

Requires the optional Python package ``edge-tts`` and network access while
generating. The finished page plays the saved files without network access.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import edge_tts

from generate_vocab_html import audio_filename, read_episodes


async def generate_one(word: str, target: Path, voice: str) -> None:
    temporary = target.with_suffix(".tmp")
    try:
        for attempt in range(3):
            try:
                await edge_tts.Communicate(word, voice, rate="-10%").save(str(temporary))
                if temporary.stat().st_size < 1000:
                    raise ValueError(f"音频文件过小：{word}")
                temporary.replace(target)
                return
            except Exception:
                temporary.unlink(missing_ok=True)
                if attempt == 2:
                    raise
                await asyncio.sleep(2 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


async def generate_all(words: list[str], output: Path, voice: str, force: bool) -> None:
    semaphore = asyncio.Semaphore(4)
    failed: list[str] = []
    created = 0

    async def work(word: str) -> None:
        nonlocal created
        target = output / audio_filename(word)
        if not force and target.is_file() and target.stat().st_size > 0:
            return
        async with semaphore:
            try:
                await generate_one(word, target, voice)
                created += 1
            except Exception as exc:
                failed.append(f"{word}: {exc}")

    await asyncio.gather(*(work(word) for word in words))
    print(f"已生成 {created} 个音频文件；共 {len(words)} 个不同词条。")
    if failed:
        raise RuntimeError("以下词条生成失败：\n" + "\n".join(failed))


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="为 Cinderella PDF 词条生成自然发音 MP3")
    parser.add_argument("--pdf", type=Path, default=script_dir / "02.Cinderella-words.pdf")
    parser.add_argument("--output", type=Path, default=script_dir / "audio")
    parser.add_argument("--voice", default="en-US-AriaNeural")
    parser.add_argument("--force", action="store_true", help="重新生成已有音频")
    args = parser.parse_args()

    words = list(dict.fromkeys(
        str(row["word"])
        for episode in read_episodes(args.pdf)
        for row in episode["rows"]
    ))
    args.output.mkdir(parents=True, exist_ok=True)
    asyncio.run(generate_all(words, args.output, args.voice, args.force))


if __name__ == "__main__":
    main()
