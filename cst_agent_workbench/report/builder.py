"""PDF report builder for CST Agent Workbench.

使用 matplotlib PDF backend 生成仿真报告，无需额外依赖。
"""
from __future__ import annotations

import io
import textwrap
from datetime import datetime
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

_BLUE = "#38bdf8"
_RED = "#f87171"
_GRAY = "#94a3b8"
_BG = "#0f172a"
_TEXT = "#e2e8f0"
_FONT = "Microsoft YaHei"


def _apply_dark_style(fig, axes):
    fig.patch.set_facecolor(_BG)
    for ax in (axes if hasattr(axes, "__iter__") else [axes]):
        ax.set_facecolor(_BG)
        ax.tick_params(colors=_TEXT)
        ax.xaxis.label.set_color(_TEXT)
        ax.yaxis.label.set_color(_TEXT)
        ax.title.set_color(_TEXT)
        for spine in ax.spines.values():
            spine.set_edgecolor(_GRAY)
        ax.grid(color=_GRAY, alpha=0.25, linewidth=0.6)


def _page_title(pdf: PdfPages, title: str, subtitle: str = ""):
    fig, ax = plt.subplots(figsize=(11, 1.2))
    _apply_dark_style(fig, ax)
    ax.axis("off")
    ax.text(0.0, 0.75, title, transform=ax.transAxes,
            fontsize=18, fontweight="bold", color=_TEXT, fontfamily=_FONT)
    if subtitle:
        ax.text(0.0, 0.15, subtitle, transform=ax.transAxes,
                fontsize=10, color=_GRAY, fontfamily=_FONT)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _table_page(pdf: PdfPages, title: str, rows: List[tuple]):
    """用 matplotlib table 渲染 key-value 列表。"""
    n = len(rows)
    fig_h = max(2.0, 0.35 * n + 1.2)
    fig, ax = plt.subplots(figsize=(11, fig_h))
    _apply_dark_style(fig, ax)
    ax.axis("off")
    ax.set_title(title, color=_TEXT, fontsize=13, fontweight="bold",
                 pad=10, loc="left", fontfamily=_FONT)
    if not rows:
        ax.text(0.05, 0.5, "（无数据）", transform=ax.transAxes, color=_GRAY)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)
        return
    col_labels = ["参数", "值"]
    tbl = ax.table(
        cellText=[[str(r[0]), str(r[1])] for r in rows],
        colLabels=col_labels,
        loc="center",
        cellLoc="left",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.35)
    for (row, col), cell in tbl.get_celld().items():
        cell.set_facecolor("#1e293b" if row % 2 == 0 else "#0f172a")
        cell.set_text_props(color=_TEXT, fontfamily=_FONT)
        cell.set_edgecolor(_GRAY)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _s11_plot_page(pdf: PdfPages, plot_data: List[Dict], target_freq: float, target_db: float):
    fig, ax = plt.subplots(figsize=(11, 5))
    _apply_dark_style(fig, ax)
    if plot_data:
        freqs = [d["freq"] for d in plot_data]
        s_db = [d["s_db"] for d in plot_data]
        ax.plot(freqs, s_db, color=_BLUE, linewidth=2, label="S11")
    if target_db:
        ax.axhline(y=target_db, color=_RED, linestyle="--", linewidth=1,
                   label=f"目标 {target_db:.1f} dB")
    if target_freq and target_freq > 0:
        ax.axvline(x=target_freq, color="#facc15", linestyle=":", linewidth=1,
                   label=f"目标频率 {target_freq:.4f} GHz")
    ax.set_xlabel("频率 (GHz)", fontfamily=_FONT)
    ax.set_ylabel("S11 (dB)", fontfamily=_FONT)
    ax.set_title("S11 仿真曲线", color=_TEXT, fontsize=13,
                 fontweight="bold", loc="left", fontfamily=_FONT)
    legend = ax.legend(facecolor="#1e293b", edgecolor=_GRAY, labelcolor=_TEXT)
    for text in legend.get_texts():
        text.set_fontfamily(_FONT)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _history_plot_page(pdf: PdfPages, history: List[Dict]):
    if not history:
        return
    rounds = [h.get("round", i + 1) for i, h in enumerate(history)]
    min_s11 = [h.get("min_s11_db") for h in history]
    target_s11 = [h.get("target_s11_db") for h in history]

    fig, ax = plt.subplots(figsize=(11, 4))
    _apply_dark_style(fig, ax)
    valid = [(r, v) for r, v in zip(rounds, min_s11) if v is not None]
    if valid:
        ax.plot([v[0] for v in valid], [v[1] for v in valid],
                color=_BLUE, marker="o", markersize=4, linewidth=1.8, label="最小 S11")
    valid_t = [(r, v) for r, v in zip(rounds, target_s11) if v is not None]
    if valid_t:
        ax.plot([v[0] for v in valid_t], [v[1] for v in valid_t],
                color="#34d399", marker="s", markersize=3, linewidth=1.4,
                linestyle="--", label="目标频率 S11")
    ax.set_xlabel("优化轮次", fontfamily=_FONT)
    ax.set_ylabel("S11 (dB)", fontfamily=_FONT)
    ax.set_title("优化历史趋势", color=_TEXT, fontsize=13,
                 fontweight="bold", loc="left", fontfamily=_FONT)
    legend = ax.legend(facecolor="#1e293b", edgecolor=_GRAY, labelcolor=_TEXT)
    for text in legend.get_texts():
        text.set_fontfamily(_FONT)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _text_page(pdf: PdfPages, title: str, body: str):
    fig, ax = plt.subplots(figsize=(11, 6))
    _apply_dark_style(fig, ax)
    ax.axis("off")
    ax.set_title(title, color=_TEXT, fontsize=13, fontweight="bold",
                 pad=10, loc="left", fontfamily=_FONT)
    wrapped = textwrap.fill(body, width=110)
    ax.text(0.02, 0.92, wrapped, transform=ax.transAxes,
            fontsize=9, color=_TEXT, fontfamily=_FONT,
            verticalalignment="top", wrap=True,
            bbox=dict(facecolor="#1e293b", edgecolor=_GRAY, boxstyle="round,pad=0.5"))
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def build_report_pdf(
    params: Optional[Dict[str, Any]] = None,
    s11_plot_data: Optional[List[Dict]] = None,
    s11_summary: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict]] = None,
    analysis_text: Optional[str] = None,
    target_freq: float = 0.0,
    target_db: float = -10.0,
    project_path: str = "",
) -> bytes:
    """生成仿真报告 PDF，返回 bytes。"""
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        # 封面
        _page_title(
            pdf,
            "CST Agent Workbench — 仿真报告",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  工程: {project_path or '未知'}",
        )

        # 建模参数
        param_rows = sorted((k, v) for k, v in (params or {}).items())
        _table_page(pdf, "建模参数", param_rows)

        # S11 结果摘要
        summary_rows = []
        if s11_summary:
            if s11_summary.get("min_s11_db") is not None:
                summary_rows.append(("最小 S11 (dB)", f"{s11_summary['min_s11_db']:.2f}"))
            if s11_summary.get("min_freq_ghz") is not None:
                summary_rows.append(("谐振频率 (GHz)", f"{s11_summary['min_freq_ghz']:.4f}"))
            if s11_summary.get("bandwidth_ghz") is not None:
                summary_rows.append(("-10dB 带宽 (GHz)", f"{s11_summary['bandwidth_ghz']:.4f}"))
            if s11_summary.get("at_f0_s11_db") is not None:
                summary_rows.append(("目标频点 S11 (dB)", f"{s11_summary['at_f0_s11_db']:.2f}"))
        if target_freq > 0:
            summary_rows.append(("目标频率 (GHz)", f"{target_freq:.4f}"))
        summary_rows.append(("目标阈值 (dB)", f"{target_db:.1f}"))
        _table_page(pdf, "S11 结果摘要", summary_rows)

        # S11 曲线图
        if s11_plot_data:
            _s11_plot_page(pdf, s11_plot_data, target_freq, target_db)

        # 优化历史趋势
        if history:
            _history_plot_page(pdf, history)
            history_rows = []
            for h in history:
                rnd = h.get("round", "-")
                strategy = h.get("strategy", "-")
                param = h.get("param_name", "-")
                delta = h.get("delta_mm", "-")
                min_s = h.get("min_s11_db")
                improved = "OK" if h.get("improved") else "--"
                history_rows.append((
                    f"轮次 {rnd}",
                    f"{strategy} | {param} delta={delta}mm | min S11: {f'{min_s:.2f}' if min_s is not None else '-'} dB | {improved}",
                ))
            _table_page(pdf, "优化历史明细", history_rows)

        # AI 分析文字
        if analysis_text and analysis_text.strip():
            _text_page(pdf, "AI 物理分析", analysis_text)

    return buf.getvalue()
