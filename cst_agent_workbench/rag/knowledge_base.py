"""RAG knowledge base for antenna design rules.

知识库条目硬编码（天线设计规则 + 调参经验），
使用 OpenAI embedding 向量化，numpy 余弦相似度检索。
对外接口：retrieve(query, client, model, top_k) -> List[str]
embedding 失败时自动降级为关键词匹配 fallback。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np

from cst_agent_workbench import config

logger = logging.getLogger(__name__)

_DYNAMIC_ENTRIES_PATH = Path(config.RAG_CACHE_DIR) / "dynamic_entries.json"
_VALID_FEED_TYPES = {"inset", "probe", "edge", "aperture", "unknown"}
_DESIGN_SIGNATURE_RE = re.compile(
    r"^er(?:lt3|3-6|6-10|gt10)_f\d+-\d+ghz_(?:inset|probe|edge|aperture|unknown)$"
)


def _coerce_design_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _er_bucket(value) -> str:
    er = _coerce_design_float(value)
    if er is None or er <= 0:
        return ""
    if er < 3:
        return "erlt3"
    if er < 6:
        return "er3-6"
    if er < 10:
        return "er6-10"
    return "ergt10"


def _freq_bucket(value) -> str:
    freq = _coerce_design_float(value)
    if freq is None or freq < 0:
        return ""
    start = int(freq)
    return f"f{start}-{start + 1}ghz"


def _feed_bucket(value) -> str:
    normalized = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    if not normalized:
        return "unknown"
    if "aperture" in normalized or "孔径" in normalized:
        return "aperture"
    if "probe" in normalized or "coax" in normalized or "探针" in normalized or "同轴" in normalized:
        return "probe"
    if "inset" in normalized or "microstrip" in normalized or "微带" in normalized or "内嵌" in normalized:
        return "inset"
    if "edge" in normalized or "边缘" in normalized:
        return "edge"
    if normalized in _VALID_FEED_TYPES:
        return normalized
    return "unknown"


def make_design_signature(er_band, freq_band, feed_type) -> str:
    """Return a stable design scope key: εr bucket + integer GHz bucket + feed type."""
    er_part = _er_bucket(er_band)
    freq_part = _freq_bucket(freq_band)
    if not er_part or not freq_part:
        return ""
    return f"{er_part}_{freq_part}_{_feed_bucket(feed_type)}"


def _normalize_design_signature(design_signature: str) -> str:
    signature = str(design_signature or "").strip().lower()
    if not signature:
        return ""
    if _DESIGN_SIGNATURE_RE.match(signature):
        return signature
    logger.warning("invalid design_signature %r; treating as universal", design_signature)
    return ""


def design_signature_from_request(request) -> str:
    if request is None:
        return ""
    return make_design_signature(
        getattr(request, "epsilon_r", None),
        getattr(request, "f0_ghz", None),
        getattr(request, "feed_strategy", None),
    )


@dataclass
class KnowledgeEntry:
    text: str
    entry_type: str = "rule"  # rule | strategy | history


KNOWLEDGE_ENTRIES: List[KnowledgeEntry] = [
    # 矩形微带贴片基本公式
    KnowledgeEntry("矩形微带贴片天线谐振频率主要由贴片长度 patch_L 决定：f0 ≈ c / (2 * patch_L_eff * sqrt(εr_eff))。增大 patch_L 使谐振频率降低，减小 patch_L 使谐振频率升高。", "rule"),
    KnowledgeEntry("矩形微带贴片的有效介电常数 εr_eff < εr，受基板厚度 substrate_h 影响：基板越厚，边缘场占空气比例越大，εr_eff 越小（Hammerstad 公式）。εr_eff 直接进入谐振频率和边缘延伸 ΔL 的计算。", "rule"),
    KnowledgeEntry("微带贴片天线的 -10dB 阻抗带宽约为 BW ≈ 3.77 * (εr-1)/εr² * (patch_W/patch_L) * (substrate_h/λ0)（Jackson 近似），增大基板厚度、增大 W/L 比或降低介电常数可拓宽带宽。", "rule"),
    # 馈电与匹配
    KnowledgeEntry("微带线馈电的输入阻抗受馈线宽度 feed_W 影响：feed_W 越宽，微带线特征阻抗越低；标准 50Ω 匹配需要根据介电常数和板厚计算合适的 feed_W。", "rule"),
    KnowledgeEntry("内嵌馈电（inset feed）通过 inset_depth 调节输入阻抗：R_in(y0) ≈ R_edge * cos²(π*y0/patch_L)，馈点越深阻抗越低。贴片边缘电阻 R_edge 典型为 150~300Ω，因此 50Ω 匹配的 inset_depth 通常落在 0.30~0.37*patch_L（只有 R_edge=100Ω 时才是 L/4）。", "rule"),
    KnowledgeEntry("S11 深度不足（> -10dB）通常说明阻抗匹配不好，应优先调整 inset_depth 或 feed_W，而不是改变 patch_L。", "strategy"),
    KnowledgeEntry("S11 谐振频率偏高说明电尺寸偏小，应增大 patch_L；频率偏低说明电尺寸偏大，应减小 patch_L。", "strategy"),
    # patch_W 作用
    KnowledgeEntry("贴片宽度 patch_W 主要影响辐射效率和增益，对谐振频率影响较小。增大 patch_W 可提高辐射效率，但会引入高次模式风险。通常 patch_W ≈ 0.9~1.1 * patch_L。", "rule"),
    # 基板参数影响
    KnowledgeEntry("增大基板厚度 substrate_h：边缘场延伸 ΔL 增大（探针馈电还叠加探针电感），净效果通常是谐振频率小幅降低；同时阻抗带宽增大、表面波损耗增大。εr_eff 减小单独看会抬高频率，但一般被 ΔL 增大的作用抵消。", "rule"),
    KnowledgeEntry("FR-4 基板典型参数：εr ≈ 4.3~4.5，tanδ ≈ 0.02；Rogers RO4003C：εr ≈ 3.55，tanδ ≈ 0.0027；基板损耗影响 S11 深度和增益。", "rule"),
    # 优化调参策略
    KnowledgeEntry("优化时每次只调一个参数，步长建议为参数当前值的 2%~5%（约 0.1~0.5mm），避免过大步长导致模式跳变。", "strategy"),
    KnowledgeEntry("若连续两轮调整同一参数同方向均未改善，说明已过调或碰到局部极值，应反向调整或换另一参数尝试。", "strategy"),
    KnowledgeEntry("谐振频率与目标偏差 < 0.5% 时，应优先调 inset_depth/feed_W 改善匹配深度，而不是继续动 patch_L。", "strategy"),
    KnowledgeEntry("谐振频率与目标偏差 > 2% 时，优先调 patch_L（大步长 0.5~1mm），快速把频率拉回目标范围再精调匹配。", "strategy"),
    # 边界条件与仿真设置
    KnowledgeEntry("天线仿真必须使用 'expanded open' 边界条件，CST 会自动添加 λ/4 空气层；使用普通 'open' 边界会引入边界反射误差。", "rule"),
    KnowledgeEntry("求解频率范围应覆盖目标频率 ±20% 以上，确保 S11 曲线完整展示谐振特征；扫频点数建议 ≥ 101。", "rule"),
    # 远场与增益
    KnowledgeEntry("矩形微带贴片天线典型增益约 5~8 dBi，最大辐射方向垂直于贴片表面（broadside）。", "rule"),
    KnowledgeEntry("远场方向图需要在仿真前设置 farfield monitor，监视器频率应设置为目标谐振频率。", "rule"),
    # 参数依赖关系
    KnowledgeEntry("patch_L 是频率控制的主参数，inset_depth 是匹配控制的主参数，feed_W 是馈线阻抗的辅助参数，三者调优顺序：先 patch_L 对频，再 inset_depth 改善匹配，最后 feed_W 微调。", "strategy"),
    KnowledgeEntry("copper_t（铜箔厚度）对谐振频率影响极小（< 0.1%），通常固定为 0.035mm（1oz 铜箔），优化时不需要调整。", "rule"),
    # 探针馈电（probe-fed / coax-fed）
    KnowledgeEntry("探针馈电（probe-fed）贴片：同轴探针从底部穿过基板连接到贴片，馈点位置距贴片中心的距离控制输入阻抗；馈点越靠近边缘阻抗越高，越靠近中心越低。", "rule"),
    KnowledgeEntry("探针馈电的探针半径和穿越基板的电感会引入串联电感效应，导致谐振频率略低于理论值，厚基板时尤为明显；可通过适当缩短 patch_L 补偿。", "rule"),
    KnowledgeEntry("探针馈电 vs 微带线馈电：探针馈电无微带辐射干扰，适合较厚基板；微带线馈电加工简单，适合薄基板（substrate_h < 0.05λ）。", "strategy"),
    # 圆极化贴片
    KnowledgeEntry("圆极化微带贴片实现方法：①截角法（在贴片对角线处截去等腰三角形，扰动量 Δ ≈ patch_L * 0.07~0.1 * sqrt(εr_eff)）；②缝隙法（在贴片中心切两条正交缝隙）。", "rule"),
    KnowledgeEntry("圆极化贴片的轴比带宽（AR < 3dB）远窄于阻抗带宽，通常仅 1~3%；对截角尺寸精度敏感，仿真中需同时监测 S11 和轴比。", "rule"),
    KnowledgeEntry("圆极化判断：左旋/右旋由截角方向和馈电位置决定；CST 中通过远场 monitor 的 Axial Ratio 结果验证，AR < 3dB 为有效圆极化频段。", "strategy"),
    # 接地板与边缘效应
    KnowledgeEntry("接地板尺寸影响天线辐射方向图和增益：接地板越大，背瓣越小，前向增益越高；有限接地板（小于 3λ×3λ）会引起边缘衍射，导致方向图出现波纹。", "rule"),
    KnowledgeEntry("接地板边缘到贴片边缘的距离建议 ≥ λ/4，过小会导致边缘衍射干扰辐射方向图，并可能降低天线效率。", "rule"),
    # 表面波
    KnowledgeEntry("表面波损耗随基板厚度增大而增大：当 substrate_h > 0.07λ/sqrt(εr) 时，表面波开始显著影响效率；低介电常数基板（εr < 2.5）表面波损耗更小，适合高效率设计。", "rule"),
    KnowledgeEntry("抑制表面波的方法：①使用低介电常数薄基板；②在天线周围加 via 围栏（EBG 结构）；③使用悬置基板结构。表面波会降低辐射效率并引起方向图畸变。", "strategy"),
    # 叠层贴片（stacked patch）
    KnowledgeEntry("叠层贴片（stacked patch）通过在主贴片上方寄生第二层贴片，利用两个谐振模式拓宽带宽，典型带宽可达 15~30%，远超单层贴片的 3~5%。", "rule"),
    KnowledgeEntry("叠层贴片调试策略：下层贴片控制主谐振，上层寄生贴片尺寸略大，两层谐振频率间隔约 5~10%；通过调整层间距和上层贴片尺寸对齐双峰 S11。", "strategy"),
    # 通用天线指标
    KnowledgeEntry("天线方向性（Directivity）D = 4π/Ω_A，单位 dBi；天线增益 G = η_rad × D，η_rad 为辐射效率；微带贴片典型效率 70~95%（FR-4 基板因损耗较低约 50~70%）。", "rule"),
    KnowledgeEntry("VSWR（电压驻波比）与 S11 关系：VSWR = (1+|S11|)/(1-|S11|)；VSWR ≤ 2 对应 |S11| ≤ -9.5dB，VSWR ≤ 1.5 对应 |S11| ≤ -14dB。", "rule"),
    KnowledgeEntry("CST 仿真收敛准则：能量精度建议设为 -40dB（默认 -30dB），网格线数建议在敏感尺寸上至少 10 条；收敛性差时检查端口定义和边界条件。", "rule"),
    # 孔径耦合馈电（aperture-coupled）
    KnowledgeEntry("孔径耦合馈电：微带馈线位于接地板下方，通过接地板上的缝隙（aperture）耦合能量到上方贴片，馈线与辐射体物理隔离，适合多层板集成；缝隙长度约 0.4~0.5 * patch_L。", "rule"),
    KnowledgeEntry("孔径耦合的耦合量由缝隙尺寸控制：缝隙越长耦合越强，输入阻抗越低；缝隙与馈线正交放置以抑制反向辐射；馈线末端开路截线长度调节匹配。", "strategy"),
    # 双频/多频贴片
    KnowledgeEntry("双频贴片实现方法：①U形缝隙贴片（在贴片刻 U 形槽，引入第二谐振）；②E形贴片；③堆叠双层贴片；④在贴片上加载短路销（shorting pin）。两个频率的阻抗带宽可独立调节。", "rule"),
    KnowledgeEntry("双频贴片调参策略：主谐振由贴片外形尺寸决定，第二谐振由缝隙/加载结构控制；两频率间距过近（< 5%）时耦合会导致双峰融合，需适当拉开间距。", "strategy"),
    # 毫米波贴片天线
    KnowledgeEntry("毫米波贴片（28GHz/60GHz/77GHz）：波长短（λ ≈ 1~10mm），基板厚度通常 < 0.2mm；加工公差对谐振频率影响显著（±0.01mm 可造成 ±100MHz 偏差）；优先选用低损耗基板（Rogers、LCP）。", "rule"),
    KnowledgeEntry("毫米波仿真要点：网格精度要求更高，建议每波长至少 20 个网格；端口应使用波导端口或集总端口（lumped port）而非模式端口；PEC 导体的表面阻抗损耗在毫米波频段不可忽略。", "rule"),
    # 缝隙天线与缝隙阵
    KnowledgeEntry("缝隙天线：在金属平面开缝隙，其辐射特性与同尺寸偶极子互补（Babinet 原理）；λ/2 缝隙谐振，输入阻抗约 363Ω；缝隙天线极化方向与缝隙长轴垂直。", "rule"),
    KnowledgeEntry("波导缝隙阵：在波导宽边周期性开斜向缝隙形成阵列，缝隙倾角控制各单元的激励幅度；适合高增益毫米波应用，波束方向与缝隙间距和波导工作频率有关。", "rule"),
    # 天线阵列
    KnowledgeEntry("线阵方向图 = 单元方向图 × 阵因子（Array Factor）；均匀线阵主瓣宽度 ≈ 0.886λ/(N×d×cosθ₀)，N 为单元数，d 为间距，θ₀ 为扫描角；间距 d > λ 时出现栅瓣。", "rule"),
    KnowledgeEntry("阵列设计原则：单元间距通常取 0.5λ（避免栅瓣同时保证足够带宽）；边缘单元因互耦效应阻抗偏离设计值，需在仿真中用无限阵（infinite array）或有限阵单独验证边缘单元性能。", "strategy"),
    KnowledgeEntry("阵列馈电网络：串联馈电（series feed）频率扫描但带宽窄；并联馈电（corporate feed）带宽宽但走线损耗大；混合馈电（series-corporate）兼顾两者，适合宽带相控阵。", "rule"),
    # S参数与端口
    KnowledgeEntry("S11 相位在谐振频率处穿越 0° 或 ±180°；S11 幅度最低点不一定是最佳匹配点，应同时检查 Smith 圆图确认阻抗轨迹是否通过圆心附近。", "strategy"),
    KnowledgeEntry("多端口天线（如 MIMO）需同时关注 S11（反射）和 S21（隔离度）；MIMO 天线单元间隔离度建议 > 20dB，可通过引入去耦结构（decoupling network）或利用极化/方向正交性实现。", "rule"),
    # 仿真调试经验
    KnowledgeEntry("仿真结果与实测偏差常见原因：①基板 εr 实际值与标称值不符（建议实测）；②加工公差（±0.05mm 级别）；③SMA 连接器未建模；④有限接地板边缘效应未考虑。", "strategy"),
    KnowledgeEntry("CST 中离散端口（discrete port）仅适合集总馈电仿真，不适合宽带扫频；宽带仿真应使用波导端口（waveguide port）或 TEM 端口，并确认端口模式与实际馈电结构匹配。", "rule"),
    KnowledgeEntry("天线效率仿真：CST 中 Total Efficiency = Radiation Efficiency × Mismatch Factor；匹配差时 Total Efficiency 会远低于 Radiation Efficiency；优化时两者都需关注。", "rule"),
    # 解析初始化公式（Pozar / Hammerstad-Jensen）
    KnowledgeEntry("矩形贴片宽度初始值：patch_W = λ₀/2 · √(2/(εr+1))，λ₀ 为自由空间波长；此公式使辐射效率最大化同时避免高次模式。", "rule"),
    KnowledgeEntry("有效介电常数：εr_eff = (εr+1)/2 + (εr-1)/2 · 1/√(1+12h/W)，h 为基板厚度，W 为贴片宽度；εr_eff < εr，随 h/W 增大而减小。", "rule"),
    KnowledgeEntry("边缘延伸修正（Hammerstad-Jensen）：ΔL = 0.412h · [(εr_eff+0.3)(W/h+0.264)] / [(εr_eff-0.258)(W/h+0.8)]；贴片有效长度 = patch_L + 2ΔL，因此实际建模 patch_L = λ₀/(2√εr_eff) - 2ΔL。", "rule"),
    KnowledgeEntry("内嵌馈电深度解析估算：inset_depth = (patch_L/π) · arccos(√(Z₀/R_edge))，R_edge ≈ 300Ω 为贴片边缘辐射电阻，Z₀=50Ω；结果限制在 0.15~0.45 × patch_L 范围内以保证物理合理性。", "rule"),
    KnowledgeEntry("50Ω 微带线宽解析：对 εr 和基板厚度 h，用 Hammerstad 公式迭代求解使特征阻抗 = 50Ω 的 feed_W；feed_W 随 εr 增大而减小，随 h 增大而增大。", "rule"),
    KnowledgeEntry("解析初始化流程（Pozar 方法）：① 给定 f0、εr、h → ② 算 patch_W → ③ 算 εr_eff → ④ 算 ΔL → ⑤ 算 patch_L → ⑥ 算 feed_W（50Ω）→ ⑦ 估算 inset_depth；以上为仿真起始尺寸，需经全波仿真进一步优化。", "strategy"),
]


_dynamic_entry_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Query rewrite (HyDE-lite): ask LLM to paraphrase the query, improving recall
# on terse / jargon-mismatched queries. Cached by (query, model, n) hash to
# avoid repeated LLM calls within a session.
# ---------------------------------------------------------------------------
_query_rewrite_cache: dict = {}
_QUERY_REWRITE_CACHE_LIMIT = 256


def _trim_query_rewrite_cache() -> None:
    """FIFO-bound the rewrite cache; it was the only unbounded store on the context path."""
    while len(_query_rewrite_cache) > _QUERY_REWRITE_CACHE_LIMIT:
        _query_rewrite_cache.pop(next(iter(_query_rewrite_cache)))
_QUERY_REWRITE_PROMPT = """You are a search-query rewriter for a Chinese antenna design knowledge base.
Given the user's query, output {n} alternate phrasings as a JSON array of strings.
Each phrasing should:
- preserve the technical intent (parameters, S11/带宽/匹配 etc.)
- vary surface form (synonyms, English/Chinese mix, different emphasis)
- be concise (each ≤ 20 Chinese chars or 12 English words)

Output ONLY the JSON array, nothing else. Example: ["...", "..."]

Query: {query}"""

_DOCUMENT_QUERY_TRANSLATION_PROMPT = """You translate user questions into search queries for the English CST Studio Suite Online Help.
Return {n} concise English retrieval queries as a JSON array of strings.
Rules:
- preserve exact CST product names, solver names, object names and parameter names
- translate the intent, not only isolated nouns
- do not answer the question
- each query must be English and no longer than 20 words

Output ONLY the JSON array.

User question: {query}"""


def _rewrite_query(query: str, client, model: str, n: int = 2, timeout: int = 6) -> List[str]:
    """Ask LLM to produce n paraphrases of the query. Returns [] on failure (caller falls back to original)."""
    if not client or not query or n <= 0:
        return []
    key = (query, model, n)
    if key in _query_rewrite_cache:
        return list(_query_rewrite_cache[key])
    try:
        prompt = _QUERY_REWRITE_PROMPT.format(n=n, query=query)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.3,
            timeout=timeout,
        )
        text = (resp.choices[0].message.content or "").strip()
        # Strip optional code fence
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
        try:
            arr = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("["), text.rfind("]")
            if start < 0 or end <= start:
                return []
            try:
                arr = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return []
        rewrites = [str(s).strip() for s in arr if isinstance(s, str) and str(s).strip()]
        rewrites = [r for r in rewrites if r != query][:n]
        _query_rewrite_cache[key] = list(rewrites)
        _trim_query_rewrite_cache()
        return rewrites
    except Exception as exc:
        logger.debug("query rewrite failed (non-critical): %s", exc)
        return []


def _translate_document_query(
    query: str,
    client,
    model: str,
    n: int = 2,
    timeout: int = 6,
) -> List[str]:
    """Return English search queries for the English CST documentation.

    This is deliberately separate from ``_rewrite_query``: curated antenna
    rules are bilingual, while the official CST corpus has an English-only
    passage contract. The translation result is cached like generic rewrites.
    """
    if not client or not query or n <= 0:
        return []
    key = ("official-doc-en", query, model, n)
    if key in _query_rewrite_cache:
        return list(_query_rewrite_cache[key])
    try:
        prompt = _DOCUMENT_QUERY_TRANSLATION_PROMPT.format(n=n, query=query)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=160,
            temperature=0.0,
            timeout=timeout,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("["), text.rfind("]")
            if start < 0 or end <= start:
                return []
            payload = json.loads(text[start:end + 1])
        translations = [
            str(item).strip()
            for item in payload
            if isinstance(item, str) and str(item).strip()
        ][:n]
        _query_rewrite_cache[key] = list(translations)
        _trim_query_rewrite_cache()
        return translations
    except Exception as exc:
        logger.debug("official-document query translation failed (non-critical): %s", exc)
        return []

# ---------------------------------------------------------------------------
# Local embedding (sentence-transformers, lazy-loaded)
# ---------------------------------------------------------------------------
_local_model = None


def _local_embed(texts: List[str]) -> np.ndarray:
    """Use a local sentence-transformers model for embedding. Free, offline."""
    global _local_model
    if _local_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            from cst_agent_workbench import config
            _local_model = SentenceTransformer(config.EMBEDDING_LOCAL_MODEL)
            logger.info("Loaded local embedding model: %s", config.EMBEDDING_LOCAL_MODEL)
        except ImportError:
            logger.error("sentence-transformers not installed. Run: pip install sentence-transformers")
            raise
    vecs = _local_model.encode(texts, normalize_embeddings=True)
    return np.array(vecs, dtype=np.float32)


def embed_texts(texts: List[str], client, model: str = "text-embedding-3-small") -> np.ndarray:
    """模块级 embedding 接口，KB 和 memory.recall_memory 共用。

    config.EMBEDDING_PROVIDER == "local" 时走本地 sentence-transformers；否则走 OpenAI API。
    返回 L2 归一化后的 (N, D) 向量，方便后续直接做点积当 cosine 用。
    """
    from cst_agent_workbench import config
    if config.EMBEDDING_PROVIDER == "local":
        return _local_embed(texts)
    import os as _os
    if _os.environ.get("EMBEDDING_API_KEY", "").strip():
        from openai import OpenAI
        client = OpenAI(api_key=config.EMBEDDING_API_KEY, base_url=config.EMBEDDING_BASE_URL or None)
        model = config.EMBEDDING_MODEL
    resp = client.embeddings.create(input=texts, model=model)
    vecs = np.array([d.embedding for d in resp.data], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vecs / norms


def _jaccard_similarity(a: str, b: str) -> float:
    def units(value: str) -> set[str]:
        tokens = {token for token in str(value or "").split() if token}
        if len(tokens) > 1:
            return tokens
        return _char_bigrams(value)

    set_a = units(a)
    set_b = units(b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


_DEDUP_THRESHOLD = 0.6
_DYNAMIC_ENTRY_LIMIT = 50


def _char_bigrams(value: str) -> set[str]:
    compact = re.sub(r"\s+", "", str(value or "").lower().replace("/", "").replace("\\", ""))
    if not compact:
        return set()
    if len(compact) == 1:
        return {compact}
    return {compact[i:i + 2] for i in range(len(compact) - 1)}


def _token_overlap_score(query: str, text: str) -> int:
    normalized_query = str(query or "").lower().replace("/", " ").replace("\\", " ")
    text_lower = str(text or "").lower()
    query_tokens = {token for token in normalized_query.split() if token}
    token_hits = sum(1 for token in query_tokens if token in text_lower)
    bigram_hits = len(_char_bigrams(normalized_query) & _char_bigrams(text_lower))
    return token_hits + bigram_hits


def _dynamic_entry_rank(entry: dict) -> tuple[float, str]:
    confidence = entry.get("confidence")
    if confidence is None:
        # 缺 confidence 的条目（如 load_knowledge_from_file 批量导入）过去默认 1.0，
        # 会永久压过带自评分的 reflection lesson（0.6~0.9），50 条满了之后动态库
        # 再也进不来新经验。给一个低于 reflection 下限的默认值，让"有据可依的新证据"
        # 能挤掉"来源不明的旧条目"。
        confidence_value = 0.5
    else:
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0
    return confidence_value, str(entry.get("timestamp") or "")


def add_dynamic_entry(
    text: str,
    entry_type: str = "history",
    confidence: Optional[float] = None,
    source: str = "runtime",
    project_scope: str = "",
    task_scope: str = "",
    design_signature: str = "",
) -> bool:
    """将一条新经验写入动态知识库（JSON 文件）。使用文件锁保证单进程多线程安全。

    confidence: 经验来源置信度（如 reflection LLM 自评分，0.0~1.0）。
    若提供且低于 config.RAG_LESSON_MIN_CONFIDENCE 阈值，跳过入库以防 RAG 毒化。
    None 表示不做置信度过滤（保持向后兼容，例如人工导入、测试用例）。

    返回 True 表示已写入，False 表示被阈值/去重/异常拦截。
    """
    if confidence is not None:
        from cst_agent_workbench import config
        if confidence < config.RAG_LESSON_MIN_CONFIDENCE:
            logger.debug(
                "dynamic entry skipped (low confidence %.2f < %.2f): %s",
                confidence, config.RAG_LESSON_MIN_CONFIDENCE, text[:60],
            )
            return False
    try:
        with _dynamic_entry_lock:
            _DYNAMIC_ENTRIES_PATH.parent.mkdir(parents=True, exist_ok=True)
            entries = []
            if _DYNAMIC_ENTRIES_PATH.exists():
                entries = json.loads(_DYNAMIC_ENTRIES_PATH.read_text(encoding="utf-8"))
            normalized_signature = _normalize_design_signature(design_signature)
            for existing in entries:
                existing_signature = _normalize_design_signature(existing.get("design_signature", ""))
                if (
                    existing.get("entry_type") == entry_type
                    and existing_signature == normalized_signature
                    and _jaccard_similarity(existing.get("text", ""), text) > _DEDUP_THRESHOLD
                ):
                    logger.debug("dynamic entry skipped (duplicate): %s", text[:60])
                    return False
            new_entry: dict = {
                "text": text,
                "entry_type": entry_type,
                "source": str(source or "runtime"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "design_signature": normalized_signature,
            }
            if confidence is not None:
                new_entry["confidence"] = float(confidence)
            if project_scope:
                new_entry["project_scope"] = str(project_scope)
            if task_scope:
                new_entry["task_scope"] = str(task_scope)
            entries.append(new_entry)
            # Keep the highest-value experiences instead of letting new low-confidence noise evict them.
            entries = sorted(entries, key=_dynamic_entry_rank, reverse=True)[:_DYNAMIC_ENTRY_LIMIT]
            # 新条目可能在这次截断里就被挤掉。此前无条件 return True 会让 reflection
            # 的 `if not written` 诊断永远不触发，经验静默丢失还报成功。
            survived = any(existing is new_entry for existing in entries)
            if not survived:
                logger.warning(
                    "dynamic entry evicted immediately (confidence=%s below top-%d): %s",
                    new_entry.get("confidence"), _DYNAMIC_ENTRY_LIMIT, text[:60],
                )
                return False
            _DYNAMIC_ENTRIES_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
            _reset_global_kb()
            logger.debug("dynamic entry added: %s", text[:60])
            return True
    except Exception as exc:
        logger.warning("add_dynamic_entry failed: %s", exc)
        return False


class AntennaKnowledgeBase:
    """向量化知识库，支持语义检索。"""

    def __init__(self):
        self._entries: List[str] = [e.text for e in KNOWLEDGE_ENTRIES]
        self._entry_types: List[str] = [e.entry_type for e in KNOWLEDGE_ENTRIES]
        # 硬编码规则隐含 confidence=1.0；只有动态条目可能携带显式置信度。
        self._confidences: List[float] = [1.0] * len(KNOWLEDGE_ENTRIES)
        self._design_signatures: List[str] = [""] * len(KNOWLEDGE_ENTRIES)
        try:
            if _DYNAMIC_ENTRIES_PATH.exists():
                dynamic = json.loads(_DYNAMIC_ENTRIES_PATH.read_text(encoding="utf-8"))
                for d in dynamic:
                    if not d.get("text"):
                        continue
                    self._entries.append(d["text"])
                    self._entry_types.append(d.get("entry_type", "history"))
                    conf = d.get("confidence")
                    self._confidences.append(float(conf) if conf is not None else 1.0)
                    self._design_signatures.append(_normalize_design_signature(d.get("design_signature", "")))
        except Exception as exc:
            logger.warning("dynamic entries load failed: %s", exc)
        self._embeddings: Optional[np.ndarray] = None  # shape (N, D)
        self._embedding_model: str = ""
        self._embedding_provider: str = ""
        self._embedding_effective_model: str = ""

    def _embed(self, texts: List[str], client, model: str) -> np.ndarray:
        return embed_texts(texts, client, model)

    def _embedding_identity(self, model: str) -> tuple[str, str]:
        import os as _os
        from cst_agent_workbench import config

        provider = str(config.EMBEDDING_PROVIDER or "openai").strip().lower()
        if provider == "local":
            return "local", str(config.EMBEDDING_LOCAL_MODEL or "").strip()
        effective_model = config.EMBEDDING_MODEL if _os.environ.get("EMBEDDING_API_KEY", "").strip() else model
        return "openai", str(effective_model or "").strip()

    def _cache_base_key(self, provider: str, effective_model: str) -> str:
        payload = json.dumps(
            {
                "entries": self._entries,
                "provider": provider,
                "model": effective_model,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]

    def _cache_dir(self) -> Path:
        return Path(config.RAG_CACHE_DIR)

    def _cache_file(self, base_key: str, dim: int) -> Path:
        return self._cache_dir() / f"{base_key}_d{dim}.npy"

    def _cache_candidates(self, base_key: str) -> list[Path]:
        cache_dir = self._cache_dir()
        if not cache_dir.exists():
            return []
        return sorted(
            cache_dir.glob(f"{base_key}_d*.npy"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def _set_embedding_state(
        self,
        embeddings: np.ndarray,
        *,
        model: str,
        provider: str,
        effective_model: str,
    ) -> None:
        self._embeddings = embeddings
        self._embedding_model = model
        self._embedding_provider = provider
        self._embedding_effective_model = effective_model

    def _current_index_matches(self, model: str, provider: str, effective_model: str) -> bool:
        if self._embeddings is None or self._embedding_model != model:
            return False
        indexed_provider = getattr(self, "_embedding_provider", "")
        indexed_model = getattr(self, "_embedding_effective_model", "")
        if not indexed_provider and not indexed_model:
            return True
        return indexed_provider == provider and indexed_model == effective_model

    def _rebuild_index(
        self,
        client,
        model: str,
        provider: str,
        effective_model: str,
        base_key: str,
    ) -> None:
        embeddings = self._embed(self._entries, client, model)
        if embeddings.ndim != 2:
            raise ValueError(f"embedding matrix must be 2-D, got shape={embeddings.shape}")
        self._set_embedding_state(
            embeddings,
            model=model,
            provider=provider,
            effective_model=effective_model,
        )

        cache_file = self._cache_file(base_key, int(embeddings.shape[1]))
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            np.save(str(cache_file), embeddings)
            logger.debug("RAG embeddings saved to cache: %s", cache_file)
        except Exception as exc:
            logger.warning("cache save failed: %s", exc)

    def _rebuild_if_dim_mismatch(self, client, model: str, expected_dim: int) -> None:
        if self._embeddings is None or self._embeddings.ndim != 2:
            return
        actual_dim = int(self._embeddings.shape[1])
        if actual_dim == expected_dim:
            return
        provider, effective_model = self._embedding_identity(model)
        base_key = self._cache_base_key(provider, effective_model)
        logger.warning(
            "RAG embedding cache dimension mismatch; 缓存维度不符，已重建: cache_dim=%s query_dim=%s",
            actual_dim,
            expected_dim,
        )
        self._rebuild_index(client, model, provider, effective_model, base_key)

    def ensure_indexed(self, client, model: str = "text-embedding-3-small") -> None:
        """首次调用时对全库向量化（lazy init），命中磁盘缓存时跳过 API 调用。"""
        provider, effective_model = self._embedding_identity(model)
        if self._current_index_matches(model, provider, effective_model):
            return

        base_key = self._cache_base_key(provider, effective_model)
        for cache_file in self._cache_candidates(base_key):
            try:
                embeddings = np.load(str(cache_file))
                if embeddings.ndim != 2 or embeddings.shape[0] != len(self._entries):
                    logger.warning("cache shape mismatch, re-embedding: %s", cache_file)
                    continue
                self._set_embedding_state(
                    embeddings,
                    model=model,
                    provider=provider,
                    effective_model=effective_model,
                )
                logger.debug("RAG embeddings loaded from cache: %s", cache_file)
                return
            except Exception as exc:
                logger.warning("cache load failed, re-embedding: %s", exc)

        self._rebuild_index(client, model, provider, effective_model, base_key)

    def retrieve(
        self,
        query: str,
        client,
        model: str = "text-embedding-3-small",
        top_k: int = 3,
        min_score: float = 0.0,
        filter_type: str | None = None,
        with_scores: bool = False,
        design_signature: str = "",
    ):
        """返回与 query 最相关的 top_k 条知识条目。

        filter_type: 按 entry_type 预过滤（rule/strategy/history），None 表示不过滤。
        min_score: cosine 相似度阈值，低于此分数的条目被过滤（至少返回1条）。
                   默认 0.0 表示不过滤；调用方可传 min_score=0.3 启用阈值切割。
        with_scores: True 时返回 List[Tuple[text, score]]，便于观测/UI 显示/二次 rerank。

        排序使用 "confidence-weighted score = cosine * (0.5 + 0.5 * confidence)"：
        - 硬编码规则 confidence=1.0，权重不变；
        - 高置信度动态条目（≥0.9）几乎不打折；
        - 写入门槛已经卡在 RAG_LESSON_MIN_CONFIDENCE，低置信不会进库，所以这里只影响中-高段的细排。
        """
        try:
            current_signature = _normalize_design_signature(design_signature)
            self.ensure_indexed(client, model)
            q_vec = self._embed([query], client, model)  # (1, D)
            self._rebuild_if_dim_mismatch(client, model, int(q_vec.shape[1]))
            raw_scores = np.atleast_1d((self._embeddings @ q_vec.T).squeeze())  # (N,)
            # confidence 加权：属性缺失或长度不匹配时安全降级为全 1.0（向后兼容）
            confidences = getattr(self, "_confidences", None)
            if confidences is not None and len(confidences) == len(self._entries):
                weights = 0.5 + 0.5 * np.array(confidences, dtype=np.float32)
            else:
                weights = np.ones(len(self._entries), dtype=np.float32)
            scores = raw_scores * weights
            if filter_type:
                candidate_indices = [i for i, entry_type in enumerate(self._entry_types) if entry_type == filter_type]
            else:
                candidate_indices = list(range(len(self._entries)))
            signatures = getattr(self, "_design_signatures", None)
            if signatures is None or len(signatures) != len(self._entries):
                signatures = [""] * len(self._entries)
            candidate_indices = [
                i for i in candidate_indices
                if not signatures[i] or signatures[i] == current_signature
            ]
            sorted_indices = sorted(candidate_indices, key=lambda i: scores[i], reverse=True)
            # min_score is a raw-cosine gate; ranking and returned scores stay confidence-weighted.
            filtered = [i for i in sorted_indices if raw_scores[i] >= min_score][:top_k]
            # 调用方显式设了阈值（如 MEMORY_RECALL_MIN_SCORE）时，"至少回一条"会把
            # 近乎随机的条目也塞进 prompt，等于把防噪门槛架空；此时宁可返回空。
            # 只有未设阈值（min_score<=0）才保留"总有兜底"的旧行为。
            if not filtered and sorted_indices and min_score <= 0.0:
                filtered = [sorted_indices[0]]
            if with_scores:
                return [(self._entries[i], float(scores[i])) for i in filtered]
            return [self._entries[i] for i in filtered]
        except Exception as exc:
            logger.warning("retrieve failed: %s", exc)
            return []


# 模块级单例，跨调用保留 embedding 缓存
_kb = AntennaKnowledgeBase()


def _keyword_fallback(
    query: str,
    top_k: int = 3,
    filter_type: str | None = None,
    design_signature: str = "",
) -> List[str]:
    """基于关键词匹配的 fallback 检索，无需 API 调用。"""
    current_signature = _normalize_design_signature(design_signature)
    if filter_type:
        kb = AntennaKnowledgeBase()
        candidate_indices = [i for i, entry_type in enumerate(kb._entry_types) if entry_type == filter_type]
        signatures = getattr(kb, "_design_signatures", None)
        if signatures is None or len(signatures) != len(kb._entries):
            signatures = [""] * len(kb._entries)
        candidate_indices = [
            i for i in candidate_indices
            if not signatures[i] or signatures[i] == current_signature
        ]
        scored = [
            (_token_overlap_score(query, kb._entries[i]), i)
            for i in candidate_indices
        ]
        scored = [item for item in scored if item[0] > 0]
        scored.sort(reverse=True)
        return [kb._entries[i] for _, i in scored[:top_k]]

    static_texts = [e.text for e in KNOWLEDGE_ENTRIES]
    scored = [
        (_token_overlap_score(query, text), index)
        for index, text in enumerate(static_texts)
    ]
    scored = [item for item in scored if item[0] > 0]
    scored.sort(reverse=True)
    if scored:
        return [static_texts[i] for _, i in scored[:top_k]]
    return static_texts[:top_k]


def _reset_global_kb() -> None:
    """Reload the module-level knowledge base after dynamic-entry writes."""
    global _kb
    try:
        _kb = AntennaKnowledgeBase()
    except NameError:
        pass


def _retrieve_multi_query(
    queries: List[str],
    client,
    embedding_model: str,
    top_k: int,
    filter_type: str | None,
    min_score: float = 0.0,
    design_signature: str = "",
) -> List[tuple]:
    """对多个 query 各召回 top_k*2，按 entry 文本聚合取最高分，返回 List[(text, score)]。

    用于 query-rewrite 模式：原 query + LLM 改写的 N 个变体，并集召回。
    """
    pool: dict[str, float] = {}
    for q in queries:
        if not q:
            continue
        pairs = _kb.retrieve(q, client, model=embedding_model, top_k=top_k * 2,
                             filter_type=filter_type, min_score=min_score, with_scores=True,
                             design_signature=design_signature)
        for text, score in pairs:
            prev = pool.get(text)
            if prev is None or score > prev:
                pool[text] = score
    return sorted(pool.items(), key=lambda x: x[1], reverse=True)[:top_k]


def _doc_store_applies(filter_type: str | None) -> bool:
    """PDF/HTML 文档库只能补充"资料"，不能冒充运行时学到的经验。

    `unified_recall` 用 filter_type="history" 专门取动态 lesson（reflection 写入、
    带 confidence 与 design_signature 作用域）。手册 chunk 三者都没有，混进去会被
    当成 agent 自己的历史经验展示给 planner，属于 provenance 污染。
    """
    return filter_type in (None, "rule", "strategy")


def _retrieve_document_texts(queries: List[str], top_k: int) -> List[str]:
    """Retrieve an independent CST-document quota across query rewrites.

    Document retrieval must not depend on how many curated rules happened to be
    returned. The previous ``top_k - len(rule_results)`` calculation starved the
    official corpus down to one hit in the normal case.
    """
    from cst_agent_workbench.rag.chroma_store import query_pdf_knowledge

    documents: List[str] = []
    seen: set[str] = set()
    for query in queries:
        if not query:
            continue
        for text in query_pdf_knowledge(query, top_k=top_k):
            clean = str(text or "").strip()
            if clean and clean not in seen:
                documents.append(clean)
                seen.add(clean)
            if len(documents) >= top_k:
                return documents
    return documents


def retrieve_official_document_hits(
    query: str,
    client=None,
    *,
    top_k: int = 3,
    rewrite: bool | None = None,
    candidate_k: int | None = None,
    deduplicate_sources: bool = True,
) -> List[dict]:
    """Return structured CST official-document hits for Agent context/trace.

    ``retrieve_antenna_rules`` keeps its legacy text API for compatibility.
    Planner, Executor and Trace need the richer representation so provenance is
    not lost when prompt text is truncated.
    """
    from cst_agent_workbench import config
    from cst_agent_workbench.rag.chroma_store import (
        is_document_store_queryable,
        query_document_knowledge,
    )
    from cst_agent_workbench.rag.reranker import rerank_document_hits

    clean_query = str(query or "").strip()
    if not clean_query or top_k <= 0:
        return []
    use_rewrite = (
        config.RAG_DOCUMENT_QUERY_TRANSLATION
        if rewrite is None
        else bool(rewrite)
    )
    rewrites: List[str] = []
    if use_rewrite and client:
        # Do not spend an LLM call translating a query when the optional
        # document store is disabled, unavailable or deliberately gated off in
        # offline tests.
        if not is_document_store_queryable():
            return []
        rewrites = _translate_document_query(
            clean_query,
            client,
            config.OPENAI_MODEL,
            n=config.RAG_QUERY_REWRITE_N,
        )

    retrieval_queries = rewrites or [clean_query]
    effective_candidate_k = (
        max(int(top_k), int(candidate_k))
        if candidate_k is not None
        else max(int(top_k), int(config.RAG_RERANK_CANDIDATE_K))
        if config.RAG_RERANK_ENABLED
        else int(top_k)
    )
    pool: dict[tuple[str, object, str], dict] = {}
    for retrieval_query in retrieval_queries:
        for raw_hit in query_document_knowledge(
            retrieval_query,
            top_k=effective_candidate_k,
            deduplicate_sources=False,
        ):
            hit = dict(raw_hit or {})
            text = str(hit.get("text") or "").strip()
            if not text:
                continue
            key = (
                str(hit.get("source_path") or hit.get("source") or ""),
                hit.get("chunk_idx"),
                text,
            )
            hit["matched_query"] = retrieval_query
            previous = pool.get(key)
            score = hit.get("score")
            previous_score = previous.get("score") if previous else None
            if previous is None or (
                score is not None
                and (previous_score is None or float(score) > float(previous_score))
            ):
                pool[key] = hit

    dense_ranked = sorted(
        pool.values(),
        key=lambda item: float(item.get("score") if item.get("score") is not None else -1.0),
        reverse=True,
    )[:effective_candidate_k]
    ranked = rerank_document_hits(retrieval_queries, dense_ranked)
    hits: List[dict] = []
    seen_sources: set[str] = set()
    for hit in ranked:
        source = str(hit.get("source_path") or hit.get("source") or "")
        if deduplicate_sources and source in seen_sources:
            continue
        seen_sources.add(source)
        hits.append(hit)
        if len(hits) >= top_k:
            break
    return hits


def retrieve_antenna_rules(
    query: str,
    client,
    embedding_model: str = "text-embedding-3-small",
    top_k: int = 3,
    filter_type: str | None = None,
    min_score: float = 0.0,
    with_scores: bool = False,
    rewrite: bool | None = None,
    design_signature: str = "",
    include_documents: bool = True,
):
    """模块级检索接口。embedding 失败时自动降级为关键词匹配 fallback。

    合并两个来源：硬编码专家规则（优先）+ ChromaDB 官方文档库（补充）。
    filter_type: 按 entry_type 预过滤（rule/strategy/history），None 表示不过滤；
                 filter_type="history" 时不并入文档库（见 `_doc_store_applies`）。
    with_scores: True 时返回 List[Tuple[text, score]]；兼容文档条目分数为 None。
    rewrite: 是否启用 query rewrite（HyDE-lite）。None=按 config.RAG_QUERY_REWRITE 决定；
             True/False 显式覆盖。
    """
    from cst_agent_workbench import config
    current_signature = _normalize_design_signature(design_signature)
    use_rewrite = config.RAG_QUERY_REWRITE if rewrite is None else bool(rewrite)
    rewrites: List[str] = []
    if use_rewrite and client:
        rewrites = _rewrite_query(query, client, config.OPENAI_MODEL, n=config.RAG_QUERY_REWRITE_N)
    queries = [query] + rewrites
    document_queries = list(queries)
    if (
        include_documents
        and config.RAG_DOCUMENT_QUERY_TRANSLATION
        and client
    ):
        from cst_agent_workbench.rag.chroma_store import is_document_store_queryable

        document_store_queryable = is_document_store_queryable()
    else:
        document_store_queryable = False
    if document_store_queryable:
        translated_document_queries = _translate_document_query(
            query,
            client,
            config.OPENAI_MODEL,
            n=config.RAG_QUERY_REWRITE_N,
        )
        if translated_document_queries:
            document_queries = translated_document_queries
    if with_scores:
        rule_pairs: List[tuple] = []
        document_results: List[str] = []
        if include_documents and _doc_store_applies(filter_type):
            try:
                document_results = _retrieve_document_texts(document_queries, top_k)
            except Exception as exc:
                logger.debug("CST document query skipped: %s", exc)
        try:
            if len(queries) > 1:
                rule_pairs = _retrieve_multi_query(
                    queries,
                    client,
                    embedding_model,
                    top_k,
                    filter_type,
                    min_score,
                    current_signature,
                )
            else:
                rule_pairs = _kb.retrieve(query, client, model=embedding_model, top_k=top_k,
                                          filter_type=filter_type, min_score=min_score, with_scores=True,
                                          design_signature=current_signature)
        except Exception as exc:
            logger.warning("RAG embedding failed, falling back to keyword match: %s", exc)
            rule_pairs = [(t, None) for t in _keyword_fallback(query, top_k, filter_type, current_signature)]
        if not rule_pairs:
            # 向量检索"有结果但都没过 min_score"与"检索本身失败"是两回事：
            # 前者是阈值主动判定为不相关，再走无阈值的关键词兜底等于绕开门槛。
            if min_score > 0.0:
                rule_pairs = []
            else:
                rule_pairs = [(t, None) for t in _keyword_fallback(query, top_k, filter_type, current_signature)]
        doc_pairs = [(f"[CST官方文档] {text}", None) for text in document_results]
        seen = {t for t, _ in rule_pairs}
        merged = list(rule_pairs)
        for text, score in doc_pairs:
            if text not in seen:
                merged.append((text, score))
                seen.add(text)
        return merged[: top_k * 2]

    rule_results: List[str] = []
    document_results: List[str] = []
    if include_documents and _doc_store_applies(filter_type):
        try:
            document_results = _retrieve_document_texts(document_queries, top_k)
        except Exception as exc:
            logger.debug("CST document query skipped: %s", exc)
    try:
        if len(queries) > 1:
            pairs = _retrieve_multi_query(
                queries,
                client,
                embedding_model,
                top_k,
                filter_type,
                min_score,
                current_signature,
            )
            rule_results = [t for t, _ in pairs]
        else:
            rule_results = _kb.retrieve(
                query,
                client,
                model=embedding_model,
                top_k=top_k,
                filter_type=filter_type,
                min_score=min_score,
                design_signature=current_signature,
            )
    except Exception as exc:
        logger.warning("RAG embedding failed, falling back to keyword match: %s", exc)
        rule_results = _keyword_fallback(query, top_k, filter_type, current_signature)
    if not rule_results:
        # 同上：阈值判定为"全部不相关"时不再走无阈值的关键词兜底。
        if min_score > 0.0:
            rule_results = []
        else:
            rule_results = _keyword_fallback(query, top_k, filter_type, current_signature)

    # 合并：专家规则在前，官方文档块加准确来源前缀后去重。
    seen = set(rule_results)
    merged = list(rule_results)
    for document_text in document_results:
        if document_text not in seen:
            merged.append(f"[CST官方文档] {document_text}")
            seen.add(document_text)
    return merged[: top_k * 2]


def load_knowledge_from_file(path: str, entry_type: str = "rule") -> int:
    """从文本文件批量导入知识条目到动态知识库。

    文件格式：每行一条知识（空行和 # 注释行跳过）。
    返回成功写入的条目数。
    """
    try:
        p = Path(path)
        if not p.exists():
            logger.warning("knowledge file not found: %s", path)
            return 0
        lines = p.read_text(encoding="utf-8").splitlines()
        count = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            add_dynamic_entry(line, entry_type=entry_type)
            count += 1
        logger.info("loaded %d entries from %s", count, path)
        return count
    except Exception as exc:
        logger.warning("load_knowledge_from_file failed: %s", exc)
        return 0
