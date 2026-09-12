# -*- coding: utf-8 -*-
"""词库健康度回归测试（不依赖 AstrBot，可离线直接跑）。

    python tests/test_prompt_dict_quality.py

为什么要有这个文件：词库是手工维护的 JSON，最容易出的不是语法错误
（那种一加载就炸，很好发现），而是**语法对但内容错**——比如某个角色名
张冠李戴（「艾雅法拉」曾错指向 ifrit）、同一个中文键在两个文件里各写
一个值（运行时后加载的静默覆盖前面的，没有任何报错）、括号里下划线
和空格混写……这类错误只会在出图时表现成「画的不对」，极难排查。

所以这里把「词库应该长什么样」写成断言，改完词库跑一遍就知道有没有
把某条搞坏。
"""
from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DICT_DIR = ROOT / "core" / "prompt_dict"
FILES = ("appearance.json", "characters.json", "scene.json")

# ---------------------------------------------------------------------------
# 让 core.prompt_dict 在没有 AstrBot 的环境下也能 import
# ---------------------------------------------------------------------------
if "astrbot" not in sys.modules:
    _astrbot = types.ModuleType("astrbot")
    _api = types.ModuleType("astrbot.api")

    class _NullLogger:
        def __getattr__(self, _name):
            return lambda *a, **k: None

    _api.logger = _NullLogger()
    _astrbot.api = _api
    sys.modules["astrbot"] = _astrbot
    sys.modules["astrbot.api"] = _api

sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_nai_plus.core.prompt_dict import (  # noqa: E402
    PromptDictionary,
    apply_dictionary,
)

PASSED = 0
FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append(msg)


def load_all() -> list[tuple[str, str, str, str]]:
    """返回 (文件, 分组, 中文键, 英文值) 的扁平列表，跳过 _comment。"""
    rows = []
    for fn in FILES:
        data = json.loads((DICT_DIR / fn).read_text(encoding="utf-8"))
        for group, items in data.items():
            if not isinstance(items, dict):
                continue
            for zh, en in items.items():
                if zh == "_comment":
                    continue
                rows.append((fn, group, zh, en))
    return rows


# ---------------------------------------------------------------------------
# 1. 结构与格式
# ---------------------------------------------------------------------------
rows = load_all()
check(len(rows) > 1500, f"词条总数异常：{len(rows)}")

for fn, group, zh, en in rows:
    check(isinstance(en, str) and en.strip() != "", f"{fn}/{group} {zh!r} 的值为空或不是字符串")
    check(zh.strip() == zh, f"{fn}/{group} 键 {zh!r} 首尾有空白")
    check(en.strip() == en, f"{fn}/{group} {zh!r} 的值 {en!r} 首尾有空白")
    check(en == en.lower() or zh == "_comment", f"{fn}/{group} {zh!r} 的值 {en!r} 含大写（NovelAI V4+ 区分大小写，标签必须全小写）")

# 括号内部不允许下划线和空格混写：`arona_(blue archive)` 这种写法两边不靠，
# Danbooru 认 `arona_(blue_archive)`，NovelAI 官方推荐 `arona (blue archive)`
_MIXED_PAREN = re.compile(r"_\([^)]*\s[^)]*\)|\s\([^)]*_[^)]*\)")
for fn, group, zh, en in rows:
    check(not _MIXED_PAREN.search(en), f"{fn}/{group} {zh!r} → {en!r} 括号内下划线/空格混写")

# ---------------------------------------------------------------------------
# 2. 重复键：不同文件/分组出现同一个中文键且值不同 → 必须为 0
#    （同值重复只是冗余，不算错，但也列出来提醒）
# ---------------------------------------------------------------------------
seen: dict[str, tuple[str, str, str]] = {}
conflicts: list[str] = []
for fn, group, zh, en in rows:
    if zh in seen:
        pf, pg, pe = seen[zh]
        if pe != en:
            conflicts.append(f"{zh!r}: {pf}/{pg}={pe!r} vs {fn}/{group}={en!r}")
    else:
        seen[zh] = (fn, group, en)
check(not conflicts, "存在值冲突的重复键（后加载的会静默覆盖前面的）：\n    " + "\n    ".join(conflicts))

# ---------------------------------------------------------------------------
# 3. 已知曾经出错的角色映射 —— 回归锁定（防止以后有人「顺手改回去」）
# ---------------------------------------------------------------------------
d = PromptDictionary()
EXPECT = {
    # 明日方舟：曾经张冠李戴
    "艾雅法拉": "eyjafjalla_(arknights)",   # 曾错为 ifrit（伊芙利特）
    "伊芙利特": "ifrit_(arknights)",
    "焰尾": "flametail_(arknights)",        # 曾错为 nearl（临光）
    "临光": "nearl_(arknights)",
    "黑键": "ebenholz_(arknights)",         # 曾错为 mudrock（泥岩）
    "泥岩": "mudrock_(arknights)",
    # 绝区零 vs 战双：曾经同键「露西亚」被战双覆盖
    "卢西娅": "lucia elowen",               # 绝区零 2.3 官方中文名「卢西娅·艾洛温」
    "露西亚": "lucia_(pgr)",                # 战双帕弥什
    # 蔚蓝档案：曾经括号内混写
    "阿罗娜": "arona (blue archive)",
    "普拉娜": "plana (blue archive)",
    # 镜头/构图重复键：保留 appearance.camera 一侧
    "正面": "front view",
    "背景虚化": "blurry background",
    "广角镜头": "wide shot",
}
for zh, want in EXPECT.items():
    got = d.lookup(zh)
    check(got == want, f"{zh!r} 期望 {want!r}，实际 {got!r}")

# ---------------------------------------------------------------------------
# 4. 纯英文键必须能命中（曾经因「不含中文直接放行」导致 20+ 条键从未生效）
# ---------------------------------------------------------------------------
for inp, want in {
    "2b": "2b (nier:automata)",
    "dva": "d.va (overwatch)",
    "saber": "artoria pendragon",
    "HK416": "hk416_(girls'_frontline)",
    "hk416": "hk416_(girls'_frontline)",     # 大小写不敏感兜底
    "MEIKO": "meiko",
    "jks": "sailor dress",
}.items():
    merged, remaining, hits, misses = apply_dictionary(inp, d)
    check(merged == want and inp in hits, f"纯英文键 {inp!r} 期望 {want!r}，实际 {merged!r}（hits={hits}）")

# 纯英文但词库没有的，必须原样保留、不进 misses（否则会白白调一次模型）
merged, remaining, hits, misses = apply_dictionary("white hair, 1girl", d)
check(merged == "white hair, 1girl" and not misses, f"未收录英文应原样保留，实际 {merged!r} misses={misses}")

# ---------------------------------------------------------------------------
# 5. 端到端：中英混排 + 空格分隔 + 消歧标签不被切碎
# ---------------------------------------------------------------------------
merged, remaining, hits, misses = apply_dictionary("白发, 2b", d)
check(merged == "white hair, 2b (nier:automata)", f"混排结果异常 {merged!r}")
merged, remaining, hits, misses = apply_dictionary("绝区零 卢西娅", d)
check(merged == "zenless zone zero, lucia elowen" and not remaining, f"绝区零 卢西娅 → {merged!r} 剩余 {remaining!r}")

# ---------------------------------------------------------------------------
# 6. v1.4.4：连接词不再阻断子串兜底 —— 整句全命中、不调模型
#    线上原句：`原神神里绫华穿着泳衣在沙滩玩耍`，以前因为「穿着」「在」
#    两个虚词导致整句交给模型（模型还把 1girl 弄丢了）。
# ---------------------------------------------------------------------------
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
sentence = "原神神里绫华穿着泳衣在沙滩玩耍"
merged, remaining, hits, misses = apply_dictionary(sentence, d)
check(remaining == "", f"整句应全命中，remaining 应为空，实际 {remaining!r}（misses={misses}）")
check(not _HAS_CJK.search(merged), f"merged 不应残留中文：{merged!r}")
check(hits and not misses, f"应有 hits 且无 misses：hits={hits} misses={misses}")
for want in ("genshin impact", "kamisato ayaka", "swimsuit", "beach", "playing"):
    check(want in merged, f"merged 缺少 {want!r}：{merged!r}")
# 连接词本身不能漏进结果（英文里当然没有，但要确认没有残留逗号垃圾）
check(",," not in merged and not merged.startswith(",") and not merged.endswith(","),
      f"merged 格式脏：{merged!r}")

# 只有连接词 + 词库词的组合都应命中
for inp in ("神里绫华穿着泳衣", "在沙滩玩耍", "一个神里绫华", "戴着帽子的神里绫华"):
    merged, remaining, hits, misses = apply_dictionary(inp, d)
    check(remaining == "" and not _HAS_CJK.search(merged), f"{inp!r} 应全命中，实际 merged={merged!r} remaining={remaining!r}")

# 反例必须仍成立：`超级赛亚人发型` 里 `亚人`→ajin 命中但残留 `超级赛`/`发型`
# 不是连接词 → 仍放弃兜底、进 misses（半截命中比不命中更危险）
merged, remaining, hits, misses = apply_dictionary("一个婴儿，超级赛亚人发型", d)
check("超级赛亚人发型" in misses, f"反例 `超级赛亚人发型` 应进 misses，实际 misses={misses} merged={merged!r}")
check("ajin" not in merged, f"反例不应把 ajin 半截塞进 merged：{merged!r}")
check("baby" in merged, f"`婴儿` 应正常命中：{merged!r}")

# 残留里混有非连接词的其他情况也要放弃：`神里绫华穿着红色的泳衣`
# （`红色` 词库若没有，则残留 `红色` 不是连接词 → 放弃整段）
merged, remaining, hits, misses = apply_dictionary("神里绫华穿着奇怪的泳衣", d)
check("神里绫华穿着奇怪的泳衣" in misses or not _HAS_CJK.search(merged),
      f"含非连接词残留的片段应整段放弃或完全命中，不能半截：merged={merged!r} misses={misses}")

# ---------------------------------------------------------------------------
# 7. v1.4.4：character_tags 只含角色名，不含作品名 / 非角色词
# ---------------------------------------------------------------------------
ct = d.character_tags
check(isinstance(ct, set) and len(ct) > 500, f"character_tags 应为较大的集合，实际 {type(ct).__name__} len={len(ct) if isinstance(ct, set) else '?'}")
for want in ("kamisato ayaka", "raiden shogun", "bianca (pgr)", "denia (wuthering waves)",
             "amiya (arknights)", "arona (blue archive)", "artoria pendragon", "hatsune miku"):
    check(want in ct, f"character_tags 应含角色 {want!r}")
for bad in ("swimsuit", "beach", "playing", "genshin impact", "wuthering waves", "blue archive",
            "arknights", "fate (series)", "nier (series)", "touhou", "overwatch",
            "zenless zone zero", "hololive", "ajin", "saiyan", "anime", "kamehameha"):
    check(bad not in ct, f"character_tags 不应含作品名/非角色词 {bad!r}")

# 词库新增词条（v1.4.4）
check(d.lookup("玩耍") == "playing" and d.lookup("嬉戏") == "playing", "新增词条 玩耍/嬉戏 → playing")
check(d.lookup("玩") is None, "不应收录单字「玩」（会误伤「玩偶」）")

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
total = PASSED + len(FAILED)
print(f"词库健康度：{PASSED}/{total} 项断言通过；词条 {len(rows)} 条（去重后 {len(d.builtin)} 条）")
if FAILED:
    print("\n未通过：")
    for f in FAILED:
        print("  ✗", f)
    sys.exit(1)
print("全部通过 ✓")
