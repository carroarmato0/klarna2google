"""Read and find on-screen elements of an Android phone over ADB (uiautomator)."""
import re
import subprocess
import xml.etree.ElementTree as ET


def adb(*args):
    return subprocess.run(["adb", *args], check=True, capture_output=True, text=True).stdout


def dump():
    adb("shell", "uiautomator", "dump", "/sdcard/k2g.xml")
    xml = adb("exec-out", "cat", "/sdcard/k2g.xml")
    nodes = []
    for n in ET.fromstring(xml).iter("node"):
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", n.get("bounds")))
        nodes.append({
            "text": n.get("text") or "",
            "desc": n.get("content-desc") or "",
            "cls": n.get("class", "").rsplit(".", 1)[-1],
            "click": n.get("clickable") == "true",
            "box": (x1, y1, x2, y2),
        })
    return nodes


def label(n):
    return n["text"] or n["desc"]


def find(nodes, value, cls=None):
    for n in nodes:
        if label(n) == value and (cls is None or n["cls"] == cls):
            return n
    return None
