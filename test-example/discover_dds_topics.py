#!/usr/bin/env python3
import argparse
import time

from cyclonedds.domain import DomainParticipant
from cyclonedds.builtin import (
    BuiltinDataReader,
    BuiltinTopicDcpsPublication,
    BuiltinTopicDcpsSubscription,
    BuiltinTopicDcpsParticipant,
)


def read_all(reader, n=500):
    try:
        return reader.read(n)
    except Exception as e:
        print("read builtin failed:", repr(e))
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--filter", default="", help="只显示包含该字符串的 topic/type")
    args = parser.parse_args()

    dp = DomainParticipant(args.domain)

    pub_reader = BuiltinDataReader(dp, BuiltinTopicDcpsPublication)
    sub_reader = BuiltinDataReader(dp, BuiltinTopicDcpsSubscription)
    par_reader = BuiltinDataReader(dp, BuiltinTopicDcpsParticipant)

    print("Discovering DDS entities...")
    print("Domain:", args.domain)
    print("Duration:", args.seconds, "seconds")
    print()

    pubs = {}
    subs = {}
    participants = set()

    t0 = time.time()
    while time.time() - t0 < args.seconds:
        for p in read_all(par_reader):
            participants.add(str(getattr(p, "key", "")))

        for ep in read_all(pub_reader):
            topic = getattr(ep, "topic_name", "")
            typ = getattr(ep, "type_name", "")
            key = (topic, typ)
            if topic:
                pubs[key] = pubs.get(key, 0) + 1

        for ep in read_all(sub_reader):
            topic = getattr(ep, "topic_name", "")
            typ = getattr(ep, "type_name", "")
            key = (topic, typ)
            if topic:
                subs[key] = subs.get(key, 0) + 1

        time.sleep(0.2)

    f = args.filter.lower()

    print("=" * 80)
    print("Participants found:", len(participants))
    print()

    print("Publishers:")
    found = False
    for (topic, typ), count in sorted(pubs.items()):
        line = f"{topic}    |    {typ}    | count={count}"
        if not f or f in line.lower():
            print("  " + line)
            found = True

    if not found:
        print("  <none matched>")

    print()
    print("Subscriptions:")
    found = False
    for (topic, typ), count in sorted(subs.items()):
        line = f"{topic}    |    {typ}    | count={count}"
        if not f or f in line.lower():
            print("  " + line)
            found = True

    if not found:
        print("  <none matched>")


if __name__ == "__main__":
    main()
