#!/usr/bin/env python3
"""Generate local MP3 pronunciation files for the PDF vocabulary.

Requires eSpeak NG and the optional Python package ``lameenc``.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import wave
from pathlib import Path

import lameenc

from generate_vocab_html import audio_filename, read_episodes


def make_mp3(word: str, espeak: str, data_parent: Path | None) -> bytes:
    command = [espeak]
    if data_parent is not None:
        command.append(f"--path={data_parent}")
    command.extend(["-v", "en-us", "-s", "145", "--stdout", word])
    result = subprocess.run(command, check=True, capture_output=True)
    with wave.open(io.BytesIO(result.stdout)) as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError(f"不支持的语音格式：{word}")
        encoder = lameenc.Encoder()
        encoder.set_in_sample_rate(wav.getframerate())
        encoder.set_channels(1)
        encoder.set_bit_rate(48)
        encoder.set_quality(2)
        return encoder.encode(wav.readframes(wav.getnframes())) + encoder.flush()


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="为 Cinderella PDF 词条生成离线发音")
    parser.add_argument("--pdf", type=Path, default=script_dir / "02.Cinderella-words.pdf")
    parser.add_argument("--output", type=Path, default=script_dir / "audio")
    parser.add_argument("--espeak", default="espeak-ng")
    parser.add_argument("--data-parent", type=Path)
    args = parser.parse_args()

    words = dict.fromkeys(
        str(row["word"])
        for episode in read_episodes(args.pdf)
        for row in episode["rows"]
    )
    args.output.mkdir(parents=True, exist_ok=True)
    created = 0
    for word in words:
        target = args.output / audio_filename(word)
        if target.is_file() and target.stat().st_size > 0:
            continue
        target.write_bytes(make_mp3(word, args.espeak, args.data_parent))
        created += 1
    print(f"已生成 {created} 个音频文件；共 {len(words)} 个不同词条。")


if __name__ == "__main__":
    main()
