#!/usr/bin/env python3
import argparse
import hashlib
from pathlib import Path
from typing import Optional


from cyclonedds.domain import DomainParticipant
from cyclonedds.topic import Topic
from cyclonedds.sub import DataReader
from cyclonedds.util import duration

# 按你的实际生成文件路径修改这一行
# 常见情况 1：
from unitree_sdk2py.idl.unitree_go.msg.dds_ import Go2FrontVideoData_

# 如果你的 Go2FrontVideoData_.py 就放在当前目录，可以改成：
# from Go2FrontVideoData_ import Go2FrontVideoData_


def to_bytes(v):
    """把 IDL sequence[uint8] 转成 bytes，便于打印/保存/解码。"""
    if v is None:
        return b""
    if isinstance(v, bytes):
        return v
    if isinstance(v, bytearray):
        return bytes(v)
    return bytes(v)


def guess_format(data: bytes) -> str:
    if not data:
        return "empty"

    h = data[:16]

    if h.startswith(b"\xff\xd8\xff"):
        return "JPEG image"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG image"
    if h.startswith(b"\x00\x00\x00\x01") or h.startswith(b"\x00\x00\x01"):
        return "H264/H265 Annex-B stream/packet"
    if len(data) > 4 and data[4:8] in (b"ftyp",):
        return "MP4/ISO-BMFF fragment"

    return "unknown/raw/compressed packet"


def dump_payload(name: str, data: bytes, sample_idx: int, save_dir: Optional[Path]):
    fmt = guess_format(data)
    sha = hashlib.sha256(data).hexdigest()[:16]
    head_hex = data[:32].hex(" ")

    print(f"  {name}:")
    print(f"    len       = {len(data)} bytes")
    print(f"    format    = {fmt}")
    print(f"    sha256[0:16] = {sha}")
    print(f"    first32   = {head_hex}")

    if save_dir and data:
        save_dir.mkdir(parents=True, exist_ok=True)

        if fmt == "JPEG image":
            suffix = "jpg"
        elif fmt == "PNG image":
            suffix = "png"
        elif fmt == "H264/H265 Annex-B stream/packet":
            suffix = "h26x"
        else:
            suffix = "bin"

        out = save_dir / f"sample_{sample_idx:06d}_{name}.{suffix}"
        out.write_bytes(data)
        print(f"    saved     = {out}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--topic",
        default="rt/frontvideostream",
        help="DDS topic 名。原生 DDS 常先试 rt/frontvideostream；不行再试 frontvideostream 或你实际的 topic。",
    )
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=5.0, help="多久收不到数据就退出，单位秒")
    parser.add_argument("--max-samples", type=int, default=10, help="最多打印多少帧/包")
    parser.add_argument("--save-dir", default="", help="可选：保存收到的视频 payload")
    args = parser.parse_args()

    save_dir = Path(args.save_dir) if args.save_dir else None

    participant = DomainParticipant(args.domain)
    topic = Topic(participant, args.topic, Go2FrontVideoData_)
    reader = DataReader(participant, topic)

    print(f"Listening DDS topic: {args.topic}")
    print(f"Type: {Go2FrontVideoData_.__idl_typename__ if hasattr(Go2FrontVideoData_, '__idl_typename__') else Go2FrontVideoData_}")
    print("Waiting data...\n")

    count = 0

    for msg in reader.take_iter(timeout=duration(seconds=args.timeout)):
        count += 1

        b720 = to_bytes(msg.video720p)
        b360 = to_bytes(msg.video360p)
        b180 = to_bytes(msg.video180p)

        print("=" * 80)
        print(f"sample #{count}")
        print(f"  time_frame = {msg.time_frame}")

        dump_payload("video720p", b720, count, save_dir)
        dump_payload("video360p", b360, count, save_dir)
        dump_payload("video180p", b180, count, save_dir)

        if count >= args.max_samples:
            break

    if count == 0:
        print("没有收到数据。请检查：")
        print("1. topic 名是否正确：可尝试 --topic rt/frontvideostream 或 --topic frontvideostream")
        print("2. DDS domain 是否正确：默认 --domain 0")
        print("3. 网卡是否配置到机器狗所在网段")
        print("4. CYCLONEDDS_URI 是否指定了正确网卡")


if __name__ == "__main__":
    main()
