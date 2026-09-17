"""知识图谱：从知识库分块中抽取病情/症状实体及其关联（共现 + 方向性线索）。

抽取策略（离线、确定性，无需大模型）：
- 实体：内置常见病/症词典在正文中匹配（长词优先，避免子串重复计数）。
- 共现边：同一分块内出现的实体两两相连，权重为共现次数。
- 方向关系：在正文中通过线索词（引发/导致/并发症/伴随/症状）判定有向关系，
  例如「高血压可引发脑卒中」→ 高血压 -cause→ 脑卒中，用于「某病情可能引发的其他关联病情」。

图谱缓存于 kb/graph.json.enc，可由 /api/kb/graph?rebuild=1 重建。
"""
import re
from collections import defaultdict, Counter

from .. import storage
from ..med_cats import CATEGORIES, TERM2CAT, categorize

# 内置医学实体词典（常见病/症/综合征）。可按需扩展。
MED_TERMS = [
    # 心脑血管
    "高血压", "糖尿病", "冠心病", "心肌梗死", "心力衰竭", "心律失常", "脑卒中", "脑梗塞", "脑出血",
    "动脉粥样硬化", "高脂血症",
    # 呼吸
    "哮喘", "慢性阻塞性肺疾病", "肺炎", "肺结核", "肺纤维化", "支气管炎", "肺气肿",
    # 消化
    "胃炎", "胃溃疡", "十二指肠溃疡", "肝炎", "肝硬化", "胆囊炎", "胆石症", "胰腺炎",
    "肠炎", "结肠炎", "便秘", "腹泻", "胃食管反流",
    # 泌尿
    "肾炎", "肾结石", "尿路感染", "尿毒症", "慢性肾病",
    # 血液
    "贫血", "白血病", "淋巴瘤", "血小板减少",
    # 内分泌
    "甲状腺功能亢进", "甲状腺功能减退", "糖尿病酮症酸中毒", "高尿酸血症", "痛风", "低血糖",
    # 风湿免疫
    "类风湿关节炎", "骨关节炎", "系统性红斑狼疮", "强直性脊柱炎", "痛风",
    # 神经精神
    "抑郁症", "焦虑症", "失眠", "癫痫", "帕金森病", "阿尔茨海默病", "偏头痛",
    # 常见症状
    "感冒", "发热", "咳嗽", "咳痰", "咽痛", "流涕", "头痛", "头晕", "乏力", "胸闷",
    "胸痛", "心悸", "气短", "腹痛", "腹胀", "恶心", "呕吐", "食欲不振",
    "皮疹", "瘙痒", "荨麻疹", "湿疹", "带状疱疹",
    "视力下降", "耳鸣", "牙痛", "牙龈炎",
    # 妇儿
    "月经不调", "痛经", "更年期综合征", "前列腺炎",
    # 骨骼肌肉
    "骨质疏松", "颈椎病", "腰椎间盘突出", "腰肌劳损",
    # 其他
    "肥胖", "营养不良", "脱水", "中暑", "过敏", "食物过敏", "药物过敏",
    "手足口病", "水痘", "麻疹", "腮腺炎", "带状疱疹",
]

# 方向性关系线索：(正则, 关系类型)
CUE_REL = [
    (re.compile(r"引发|诱发|导致|引起|可致|可并发于|易并发"), "cause"),   # 可能引发
    (re.compile(r"并发症|并发"), "complication"),                         # 并发症
    (re.compile(r"伴随|合并|常伴有|可伴|继发"), "coexist"),               # 伴随/继发
    (re.compile(r"症状|表现|临床|体征"), "symptom"),                      # 症状/表现
]

REL_LABEL = {
    "cause": "可能引发", "complication": "并发症", "coexist": "伴随/继发",
    "symptom": "症状关联", "related": "相关",
}


def _extract_terms(text: str) -> list[str]:
    found = []
    seen_pos = set()
    for term in sorted(MED_TERMS, key=len, reverse=True):
        start = 0
        while True:
            idx = text.find(term, start)
            if idx < 0:
                break
            end = idx + len(term)
            if any(p in seen_pos for p in range(idx, end)):
                start = end
                continue
            found.append(term)
            seen_pos.update(range(idx, end))
            start = end
    return found


def _detect_relations(text: str, terms: list[str]) -> list[tuple]:
    """在同一小句（线索词前后各 REL_WINDOW 字符内）抽取有向关系，避免跨句误连。"""
    rels = []
    REL_WINDOW = 24
    # 每个词的全部出现位置（用于就近匹配，而非仅首现）
    term_hits = {}
    for t in set(terms):
        hits = []
        start = 0
        while True:
            idx = text.find(t, start)
            if idx < 0:
                break
            hits.append(idx)
            start = idx + len(t)
        if hits:
            term_hits[t] = hits
    flat = sorted([(p, t) for t, hits in term_hits.items() for p in hits])
    for cue_re, rel_type in CUE_REL:
        for m in cue_re.finditer(text):
            pos = m.start()
            before = [(p, t) for (p, t) in flat if p < pos and pos - p <= REL_WINDOW]
            after = [(p, t) for (p, t) in flat if p > pos and p - pos <= REL_WINDOW]
            if before and after:
                a = before[-1][1]
                b = after[0][1]
                if a != b:
                    rels.append((a, b, rel_type))
    return rels


def _load_all_chunks(user_id: str) -> list[dict]:
    docs = storage.load(user_id, "kb/docs.json.enc", {}) or {}
    chunks = []
    for doc_id, meta in docs.items():
        doc_chunks = storage.load(user_id, f"kb/chunks/{doc_id}.json.enc", [])
        if isinstance(doc_chunks, dict):
            doc_chunks = doc_chunks.get("chunks", [])
        for i, c in enumerate(doc_chunks):
            chunks.append({"doc_id": doc_id, "doc_name": meta.get("name", ""),
                           "chunk_idx": i, "text": c})
    return chunks


def build_graph(user_id: str) -> dict:
    chunks = _load_all_chunks(user_id)
    nodes: dict = {}
    cooc = defaultdict(int)
    directed = defaultdict(Counter)  # (a, b) -> Counter(rel_type)

    for c in chunks:
        terms = _extract_terms(c["text"])
        for t in terms:
            n = nodes.setdefault(t, {"id": t, "label": t, "freq": 0, "docs": set()})
            n["freq"] += 1
            n["docs"].add(c["doc_id"])
        for i in range(len(terms)):
            for j in range(i + 1, len(terms)):
                a, b = terms[i], terms[j]
                key = (a, b) if a < b else (b, a)
                cooc[key] += 1
        for a, b, rel in _detect_relations(c["text"], terms):
            directed[(a, b)][rel] += 1

    links = []
    for (a, b), w in cooc.items():
        rel_a = directed.get((a, b))
        rel_b = directed.get((b, a))
        raw = list(rel_a.elements()) if rel_a else (list(rel_b.elements()) if rel_b else [])
        rels = list(dict.fromkeys(raw))  # 去重，仅保留出现过的关联类型
        is_dir = bool(rel_a) or bool(rel_b)
        links.append({"source": a, "target": b, "weight": w,
                      "rels": rels, "directed": is_dir})

    directed_links = []
    for (a, b), cnt in directed.items():
        directed_links.append({"source": a, "target": b,
                               "rels": list(cnt.keys()),
                               "weight": sum(cnt.values())})

    node_list = [{"id": k, "label": v["label"], "freq": v["freq"],
                  "doc_count": len(v["docs"]),
                  "category": TERM2CAT.get(k, categorize(k))}
                 for k, v in nodes.items()]

    # 分层树：仅含出现过的分类，按 CATEGORIES 顺序排列，简洁清晰
    by_cat: dict[str, list[str]] = defaultdict(list)
    for n in node_list:
        by_cat[n["category"]].append(n["id"])
    tree = [{"category": c, "count": len(by_cat.get(c, [])),
             "nodes": sorted(by_cat.get(c, []))} for c in CATEGORIES if c in by_cat]

    return {"nodes": node_list, "links": links,
            "directed_links": directed_links, "rel_label": REL_LABEL,
            "tree": tree, "categories": [t["category"] for t in tree]}
