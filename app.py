# app.py
# AURA-7 Hub (Advanced Streamlit App) — HF Spaces Ready
# - Default model: gemini-3.1-flash-lite
# - Supports: Gemini API + OpenAI API (optional)
# - Modules: Distribution & Dataset (6 graphs incl GIS chain), AI Ops Dashboard (6 graphs),
#            AI Note Keeper (basic), Skill.md & Prompt Lab (upload/paste/edit/download, improve via agent, apply to doc)
# - Adds: WOW LLM execution visualization (timeline, indicators), live logs, robust error handling
#
# NOTE:
# - This file intentionally avoids external services that require tokens (e.g., Mapbox).
# - GIS rendering uses Plotly + open-street-map tiles (no token required).
#
# Security:
# - API keys are never printed to logs.
# - If env key exists, UI does NOT reveal it.
# - If env key does not exist, user can input key (stored in session_state only).

from __future__ import annotations

import os
import re
import io
import json
import time
import math
import textwrap
import hashlib
import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Iterable

import pandas as pd
import numpy as np
import streamlit as st

# Plotly for interactive graphs
import plotly.express as px
import plotly.graph_objects as go

# ----------------------------
# App Constants / Defaults
# ----------------------------

APP_TITLE = "AURA-7 Hub — Agentic Geospatial Compliance Audit Platform"
DEFAULT_LANGUAGE = "繁體中文"
SUPPORTED_LANGUAGES = ["繁體中文", "English"]

DEFAULT_PROVIDER = "Gemini"
SUPPORTED_PROVIDERS = ["Gemini", "OpenAI"]

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"

# Pantone-inspired style presets (simple palette tokens)
PANTONE_STYLES = {
    "Living Coral": {"primary": "#FF6F61", "bg": "#0B0F14", "card": "#111827", "text": "#E5E7EB"},
    "Classic Blue": {"primary": "#0F4C81", "bg": "#0B0F14", "card": "#111827", "text": "#E5E7EB"},
    "Ultimate Gray": {"primary": "#939597", "bg": "#0B0F14", "card": "#111827", "text": "#E5E7EB"},
    "Very Peri": {"primary": "#6667AB", "bg": "#0B0F14", "card": "#111827", "text": "#E5E7EB"},
    "Emerald": {"primary": "#009B77", "bg": "#0B0F14", "card": "#111827", "text": "#E5E7EB"},
    "Peach Fuzz": {"primary": "#FFBE98", "bg": "#0B0F14", "card": "#111827", "text": "#E5E7EB"},
    "Radiant Orchid": {"primary": "#B565A7", "bg": "#0B0F14", "card": "#111827", "text": "#E5E7EB"},
    "Illuminating": {"primary": "#F5DF4D", "bg": "#0B0F14", "card": "#111827", "text": "#E5E7EB"},
    "Marsala": {"primary": "#955251", "bg": "#0B0F14", "card": "#111827", "text": "#E5E7EB"},
    "Tangerine Tango": {"primary": "#DD4124", "bg": "#0B0F14", "card": "#111827", "text": "#E5E7EB"},
}

DEFAULT_STYLE = "Living Coral"
DEFAULT_THEME_MODE = "Dark"  # "Light" / "Dark"

# Data column canonical names for Distribution/Purchase unified schema
CANON_COLS = [
    "event_type",         # distribution/purchase/usage/return/transfer
    "event_datetime",     # datetime64[ns]
    "date_zone",          # derived label (string)
    "supplier_id",
    "customer_id",
    "license_no",
    "model_no",
    "udi",
    "sn",
    "lot",
    "quantity",
    "from_entity_id",
    "to_entity_id",
    "from_lat",
    "from_lng",
    "to_lat",
    "to_lng",
    "region",
    "city",
    "postal_code",
    "compliance_flags",   # list/pipe-separated string
    "source_dataset",     # distribution/purchase
]

# ----------------------------
# Utilities
# ----------------------------

def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def safe_hash(obj: Any) -> str:
    try:
        b = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    except Exception:
        b = str(obj).encode("utf-8", errors="ignore")
    return hashlib.sha256(b).hexdigest()[:12]

def redact_secrets(text: str) -> str:
    """Redact likely secrets from text (API keys, tokens). Conservative patterns."""
    if not text:
        return text
    patterns = [
        r"(?i)\bsk-[a-z0-9]{20,}\b",                  # OpenAI-like
        r"(?i)\bAIza[0-9A-Za-z\-_]{20,}\b",           # Google API key-like
        r"(?i)\bya29\.[0-9A-Za-z\-_]+\b",             # OAuth token-like
        r"(?i)\b[A-Za-z0-9_\-]{30,}\b",               # generic long token
    ]
    redacted = text
    for p in patterns:
        redacted = re.sub(p, "[REDACTED_TOKEN]", redacted)
    return redacted

def ensure_session_defaults() -> None:
    ss = st.session_state
    ss.setdefault("language", DEFAULT_LANGUAGE)
    ss.setdefault("theme_mode", DEFAULT_THEME_MODE)
    ss.setdefault("style_name", DEFAULT_STYLE)

    ss.setdefault("provider", DEFAULT_PROVIDER)
    ss.setdefault("gemini_model", DEFAULT_GEMINI_MODEL)
    ss.setdefault("openai_model", DEFAULT_OPENAI_MODEL)

    ss.setdefault("override_env_key", False)
    ss.setdefault("gemini_api_key_input", "")
    ss.setdefault("openai_api_key_input", "")

    ss.setdefault("live_logs", [])
    ss.setdefault("runs", [])  # AI Ops: list of run metadata dicts

    ss.setdefault("distribution_df", None)
    ss.setdefault("purchase_df", None)
    ss.setdefault("unified_df", None)

    ss.setdefault("skill_md_text", default_skill_md())
    ss.setdefault("prompt_library", default_prompt_library())
    ss.setdefault("active_prompt_name", "Skill Improver (Strict)")

    ss.setdefault("note_text", "")
    ss.setdefault("note_output_md", "")
    ss.setdefault("doc_input_text", "")
    ss.setdefault("doc_output_text", "")

def log_event(level: str, msg: str, meta: Optional[Dict[str, Any]] = None) -> None:
    meta = meta or {}
    entry = {
        "ts": now_iso(),
        "level": level.upper(),
        "msg": redact_secrets(msg),
        "meta": {k: redact_secrets(str(v)) for k, v in meta.items()},
    }
    st.session_state.live_logs.append(entry)

def inject_css(theme_mode: str, style_name: str) -> None:
    style = PANTONE_STYLES.get(style_name, PANTONE_STYLES[DEFAULT_STYLE])
    primary = style["primary"]

    if theme_mode == "Light":
        bg = "#F6F7FB"
        card = "#FFFFFF"
        text = "#0B1220"
        subtle = "#6B7280"
        border = "rgba(17,24,39,0.12)"
    else:
        bg = style["bg"]
        card = style["card"]
        text = style["text"]
        subtle = "#9CA3AF"
        border = "rgba(255,255,255,0.12)"

    css = f"""
    <style>
      .stApp {{
        background: {bg};
        color: {text};
      }}
      .a7-card {{
        background: {card};
        border: 1px solid {border};
        border-radius: 14px;
        padding: 14px 14px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.12);
      }}
      .a7-kpi {{
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
      }}
      .a7-kpi > div {{
        background: {card};
        border: 1px solid {border};
        border-radius: 14px;
        padding: 10px 12px;
        min-width: 160px;
      }}
      .a7-accent {{
        color: {primary};
        font-weight: 700;
      }}
      .a7-subtle {{
        color: {subtle};
      }}
      .a7-badge {{
        display: inline-block;
        border: 1px solid {border};
        padding: 2px 10px;
        border-radius: 999px;
        background: rgba(255,255,255,0.03);
        font-size: 12px;
      }}
      .a7-hr {{
        height: 1px; background: {border}; border: none; margin: 10px 0;
      }}
      /* Live log styling */
      .a7-log {{
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        font-size: 12px;
        line-height: 1.35;
      }}
      /* Keyword coral highlight */
      .kw-coral {{
        color: {PANTONE_STYLES["Living Coral"]["primary"]};
        font-weight: 700;
      }}
      /* Streamlit widget polish */
      [data-testid="stSidebar"] > div {{
        background: {card};
      }}
      /* Make headers pop */
      h1, h2, h3 {{
        letter-spacing: 0.2px;
      }}
      /* Buttons accent */
      .stButton > button {{
        border-radius: 10px;
        border: 1px solid {border};
      }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)

def default_skill_md() -> str:
    return """# skill.md — AURA-7 Agent Skills (Baseline)

## Purpose
This skill file defines how the agent should analyze regulatory supply-chain data, generate structured outputs, and avoid hallucinations.

## Core Rules
1. **Be audit-grade**: always provide traceable, structured reasoning with explicit assumptions.
2. **No secrets**: never output API keys or tokens; redact sensitive identifiers if present.
3. **Use only provided facts**: if a fact is missing, state it clearly and propose verification steps.
4. **Output format**: prefer Markdown with headings, tables, and action items.

## Output Templates
### Executive Summary
- Key findings (3–7 bullets)
- Risk hotspots
- Suggested next actions

### Evidence Table
| Field | Value | Source | Confidence |
|---|---:|---|---:|

### Compliance Recommendations
- Immediate containment
- Follow-up audit questions
- Data quality improvements
"""

def default_prompt_library() -> Dict[str, str]:
    return {
        "Skill Improver (Strict)": """You are a senior agent architect. Improve the provided skill.md for:
- regulatory audit rigor
- anti-hallucination constraints
- explicit output schemas
- bilingual (zh-TW + English) behavior
- safe handling of secrets

Requirements:
1) Return ONLY the improved skill.md in Markdown.
2) Preserve original intent but expand with clearer rules, sections, and templates.
3) Add a "Data Schema" section for distribution/purchase datasets and filters.
4) Add a "Graph Narration" section describing how to narrate insights from 6 graphs.
""",
        "Apply Skill.md to Document (Audit)": """You are an audit agent. Use the provided skill.md as your operating instructions.
Task: analyze the provided document and produce an audit-grade structured output.

Requirements:
- Follow skill.md strictly.
- Provide an Executive Summary, Evidence Table, and Action List.
- If the document lacks key fields (UDI/SN/LOT/license/model/date), list missing fields and propose verification questions.
Return Markdown only.
""",
        "Note Organizer (Coral Keywords)": """You are an AI Note Keeper.
Transform the input into organized Markdown:
- Use headings and bullet points.
- Extract 8-15 keywords and wrap them in <span class="kw-coral">KEYWORD</span>.
- End with a short checklist.
Return Markdown only.
"""
    }

# ----------------------------
# LLM Provider Abstraction
# ----------------------------

@dataclass
class LLMConfig:
    provider: str
    model: str
    api_key: str
    temperature: float = 0.2
    max_output_tokens: int = 2048

class LLMError(RuntimeError):
    pass

def get_env_key(provider: str) -> str:
    if provider == "Gemini":
        return os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
    if provider == "OpenAI":
        return os.environ.get("OPENAI_API_KEY", "")
    return ""

def resolve_api_key(provider: str) -> Tuple[str, str]:
    """
    Returns (api_key, key_source) where key_source is 'env' or 'user' or ''.
    """
    env_key = get_env_key(provider)
    if env_key and not st.session_state.get("override_env_key", False):
        return env_key, "env"

    if provider == "Gemini":
        k = st.session_state.get("gemini_api_key_input", "").strip()
    else:
        k = st.session_state.get("openai_api_key_input", "").strip()

    if k:
        return k, "user"
    return "", ""

def list_models(provider: str) -> List[str]:
    # Keep conservative hardcoded lists to avoid API calls.
    if provider == "Gemini":
        return [
            "gemini-3.1-flash-lite",
            "gemini-3.1-flash",
            "gemini-3.1-pro",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
        ]
    return [
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-4o-mini",
        "gpt-4o",
        "o4-mini",
    ]

def _call_gemini(cfg: LLMConfig, system: str, user: str) -> Tuple[str, Dict[str, Any]]:
    """
    Gemini call with best-effort compatibility across google-genai / google.generativeai.
    Returns (text, usage_meta).
    """
    usage = {"provider": "Gemini", "model": cfg.model, "input_tokens": None, "output_tokens": None}
    last_err = None

    # Try google-genai (newer)
    try:
        from google import genai  # type: ignore
        client = genai.Client(api_key=cfg.api_key)
        # Some Gemini models accept system via "contents" parts; keep it simple and robust:
        prompt = f"{system}\n\n---\n\n{user}".strip()
        t0 = time.time()
        resp = client.models.generate_content(
            model=cfg.model,
            contents=prompt,
            config={
                "temperature": cfg.temperature,
                "max_output_tokens": cfg.max_output_tokens,
            },
        )
        dt_ms = int((time.time() - t0) * 1000)
        text = getattr(resp, "text", None) or (resp.candidates[0].content.parts[0].text if resp and resp.candidates else "")
        usage.update({"latency_ms": dt_ms})
        return text or "", usage
    except Exception as e:
        last_err = e

    # Try google.generativeai (older)
    try:
        import google.generativeai as genai  # type: ignore
        genai.configure(api_key=cfg.api_key)
        model = genai.GenerativeModel(cfg.model, system_instruction=system)
        t0 = time.time()
        resp = model.generate_content(
            user,
            generation_config={
                "temperature": cfg.temperature,
                "max_output_tokens": cfg.max_output_tokens,
            },
        )
        dt_ms = int((time.time() - t0) * 1000)
        text = getattr(resp, "text", "") or ""
        usage.update({"latency_ms": dt_ms})
        return text, usage
    except Exception as e:
        last_err = e

    raise LLMError(f"Gemini call failed. {type(last_err).__name__}: {redact_secrets(str(last_err))}")

def _call_openai(cfg: LLMConfig, system: str, user: str) -> Tuple[str, Dict[str, Any]]:
    usage = {"provider": "OpenAI", "model": cfg.model, "input_tokens": None, "output_tokens": None}
    try:
        from openai import OpenAI  # type: ignore
        client = OpenAI(api_key=cfg.api_key)
        t0 = time.time()
        resp = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=cfg.temperature,
            max_tokens=cfg.max_output_tokens,
        )
        dt_ms = int((time.time() - t0) * 1000)
        text = resp.choices[0].message.content or ""
        # best-effort usage
        if getattr(resp, "usage", None):
            usage["input_tokens"] = getattr(resp.usage, "prompt_tokens", None)
            usage["output_tokens"] = getattr(resp.usage, "completion_tokens", None)
        usage.update({"latency_ms": dt_ms})
        return text, usage
    except Exception as e:
        raise LLMError(f"OpenAI call failed. {type(e).__name__}: {redact_secrets(str(e))}")

def llm_generate(cfg: LLMConfig, system: str, user: str) -> Tuple[str, Dict[str, Any]]:
    if cfg.provider == "Gemini":
        return _call_gemini(cfg, system, user)
    if cfg.provider == "OpenAI":
        return _call_openai(cfg, system, user)
    raise LLMError(f"Unsupported provider: {cfg.provider}")

def wow_run_llm(
    title: str,
    cfg: LLMConfig,
    system: str,
    user: str,
    show_prompt_expander: bool = True,
) -> str:
    """
    Provides WOW execution visualization:
    - timeline stages
    - indicators
    - streaming-like output (chunked)
    - logs + run metadata recorded for AI Ops dashboard
    """
    system = redact_secrets(system or "")
    user = redact_secrets(user or "")

    run_id = f"run_{safe_hash({'t': time.time(), 'title': title, 'provider': cfg.provider, 'model': cfg.model})}"
    filter_snapshot = st.session_state.get("active_filter_snapshot", {})
    dataset_snapshot = st.session_state.get("active_dataset_snapshot", {})
    meta_base = {
        "run_id": run_id,
        "title": title,
        "provider": cfg.provider,
        "model": cfg.model,
        "ts": now_iso(),
        "filter_snapshot_hash": safe_hash(filter_snapshot),
        "dataset_snapshot_hash": safe_hash(dataset_snapshot),
    }

    log_event("AUDIT", f"LLM run started: {title}", meta=meta_base)

    card = st.container()
    with card:
        st.markdown(f"<div class='a7-card'>", unsafe_allow_html=True)
        st.markdown(f"### {title}")
        cols = st.columns([1.2, 1.2, 1.2, 1.2])
        cols[0].markdown(f"<span class='a7-badge'>Provider: <span class='a7-accent'>{cfg.provider}</span></span>", unsafe_allow_html=True)
        cols[1].markdown(f"<span class='a7-badge'>Model: <span class='a7-accent'>{cfg.model}</span></span>", unsafe_allow_html=True)
        cols[2].markdown(f"<span class='a7-badge'>Temp: <span class='a7-accent'>{cfg.temperature}</span></span>", unsafe_allow_html=True)
        cols[3].markdown(f"<span class='a7-badge'>Max Out: <span class='a7-accent'>{cfg.max_output_tokens}</span></span>", unsafe_allow_html=True)
        st.markdown("<hr class='a7-hr'/>", unsafe_allow_html=True)

        # Timeline
        stages = [
            ("Input sanitize", 0),
            ("Prompt assembly", 0),
            ("Provider call", 0),
            ("Streaming decode", 0),
            ("Post-check", 0),
            ("Render", 0),
        ]
        stage_status = {name: "pending" for name, _ in stages}
        stage_time = {name: 0 for name, _ in stages}

        timeline_placeholder = st.empty()
        output_placeholder = st.empty()
        indicator_cols = st.columns([1, 1, 1])

        def render_timeline():
            rows = []
            for s, _ in stages:
                stt = stage_status[s]
                ms = stage_time[s]
                rows.append({"Stage": s, "Status": stt, "ms": ms})
            df = pd.DataFrame(rows)
            timeline_placeholder.dataframe(df, use_container_width=True, hide_index=True)

        def set_stage(name: str, status: str, ms: int):
            stage_status[name] = status
            stage_time[name] = ms
            render_timeline()

        render_timeline()

        # Prompt expander (safe)
        if show_prompt_expander:
            with st.expander("View assembled prompt (secrets redacted)"):
                st.markdown("**System**")
                st.code(system[:8000] if system else "", language="markdown")
                st.markdown("**User**")
                st.code(user[:8000] if user else "", language="markdown")

        # Execute with stage timings
        t0 = time.time()
        try:
            s0 = time.time()
            set_stage("Input sanitize", "running", 0)
            time.sleep(0.05)
            set_stage("Input sanitize", "success", int((time.time() - s0) * 1000))

            s1 = time.time()
            set_stage("Prompt assembly", "running", 0)
            # Nothing heavy; but keep stage for traceability
            assembled_system = system
            assembled_user = user
            set_stage("Prompt assembly", "success", int((time.time() - s1) * 1000))

            s2 = time.time()
            set_stage("Provider call", "running", 0)
            text, usage = llm_generate(cfg, assembled_system, assembled_user)
            provider_ms = int((time.time() - s2) * 1000)
            set_stage("Provider call", "success", provider_ms)

            # Indicators (best-effort)
            latency_ms = usage.get("latency_ms", provider_ms)
            input_tokens = usage.get("input_tokens", None)
            output_tokens = usage.get("output_tokens", None)

            indicator_cols[0].metric("Latency (ms)", f"{latency_ms}")
            indicator_cols[1].metric("Input tokens", "-" if input_tokens is None else f"{input_tokens}")
            indicator_cols[2].metric("Output tokens", "-" if output_tokens is None else f"{output_tokens}")

            # Chunked streaming display (WOW effect)
            s3 = time.time()
            set_stage("Streaming decode", "running", 0)
            chunks = []
            text = text or ""
            # Avoid freezing on huge text
            max_chars = 20000
            if len(text) > max_chars:
                text = text[:max_chars] + "\n\n[TRUNCATED FOR UI SAFETY]"
            step = 220
            for i in range(0, len(text), step):
                chunks.append(text[i:i+step])
            buf = ""
            for c in chunks:
                buf += c
                output_placeholder.markdown(buf)
                time.sleep(0.01)
            set_stage("Streaming decode", "success", int((time.time() - s3) * 1000))

            s4 = time.time()
            set_stage("Post-check", "running", 0)
            # Post-check: ensure no secrets leaked
            cleaned = redact_secrets(buf)
            set_stage("Post-check", "success", int((time.time() - s4) * 1000))

            s5 = time.time()
            set_stage("Render", "running", 0)
            output_placeholder.markdown(cleaned)
            set_stage("Render", "success", int((time.time() - s5) * 1000))

            total_ms = int((time.time() - t0) * 1000)

            run_record = {
                **meta_base,
                "latency_ms": total_ms,
                "provider_latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "ok": True,
                "error": "",
            }
            st.session_state.runs.append(run_record)
            log_event("AUDIT", f"LLM run success: {title}", meta=run_record)

            st.markdown("</div>", unsafe_allow_html=True)
            return cleaned

        except Exception as e:
            total_ms = int((time.time() - t0) * 1000)
            set_stage("Provider call", "fail", total_ms)
            err = f"{type(e).__name__}: {redact_secrets(str(e))}"
            st.error(err)
            run_record = {
                **meta_base,
                "latency_ms": total_ms,
                "provider_latency_ms": None,
                "input_tokens": None,
                "output_tokens": None,
                "ok": False,
                "error": err,
            }
            st.session_state.runs.append(run_record)
            log_event("ERROR", f"LLM run failed: {title}", meta=run_record)
            st.markdown("</div>", unsafe_allow_html=True)
            return ""

# ----------------------------
# Data Ingestion & Unification
# ----------------------------

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]
    return df

def parse_datetime_series(s: pd.Series) -> pd.Series:
    # robust parse; keep NaT if parse fails
    return pd.to_datetime(s, errors="coerce", utc=False)

def best_effort_map_columns(df: pd.DataFrame) -> Dict[str, str]:
    """
    Heuristic mapping from common column names to canonical.
    Users can still fix mapping in future iterations; here we provide minimal robust.
    """
    colmap = {}
    cols = {c.lower(): c for c in df.columns}

    def find(*names):
        for n in names:
            if n.lower() in cols:
                return cols[n.lower()]
        return None

    candidates = {
        "event_datetime": ["event_datetime", "datetime", "date_time", "date", "交易日期", "出庫日期", "入庫日期", "簽收日期"],
        "supplier_id": ["supplier_id", "supplier", "供應商id", "供應商", "vendor_id"],
        "customer_id": ["customer_id", "customer", "客戶id", "醫院id", "buyer_id"],
        "license_no": ["license_no", "license", "許可證字號", "許可證", "license number"],
        "model_no": ["model_no", "model", "型號", "model number"],
        "udi": ["udi", "gs1", "barcode", "udi碼", "條碼"],
        "sn": ["sn", "serial", "serial_number", "序號", "序列號"],
        "lot": ["lot", "batch", "批號", "lot_no"],
        "quantity": ["quantity", "qty", "數量"],
        "from_entity_id": ["from_entity_id", "from", "出貨方", "發貨方"],
        "to_entity_id": ["to_entity_id", "to", "收貨方", "到貨方", "醫院"],
        "from_lat": ["from_lat", "出貨緯度"],
        "from_lng": ["from_lng", "from_lon", "出貨經度"],
        "to_lat": ["to_lat", "收貨緯度"],
        "to_lng": ["to_lng", "to_lon", "收貨經度"],
        "region": ["region", "區域"],
        "city": ["city", "縣市"],
        "postal_code": ["postal_code", "zip", "郵遞區號"],
    }

    for canon, names in candidates.items():
        hit = find(*names)
        if hit:
            colmap[canon] = hit

    return colmap

def unify_dataset(df: pd.DataFrame, dataset_type: str) -> pd.DataFrame:
    """
    Convert a dataset into canonical event schema. Adds missing columns with defaults.
    dataset_type: 'distribution' or 'purchase'
    """
    df = normalize_columns(df)
    mapping = best_effort_map_columns(df)
    out = pd.DataFrame()

    # Basic columns
    out["source_dataset"] = dataset_type
    out["event_type"] = dataset_type

    # Datetime
    if "event_datetime" in mapping:
        out["event_datetime"] = parse_datetime_series(df[mapping["event_datetime"]])
    else:
        out["event_datetime"] = pd.NaT

    # Others
    for canon in ["supplier_id", "customer_id", "license_no", "model_no", "udi", "sn", "lot", "region", "city", "postal_code"]:
        out[canon] = df[mapping[canon]].astype(str) if canon in mapping else ""

    # Quantity numeric
    if "quantity" in mapping:
        q = pd.to_numeric(df[mapping["quantity"]], errors="coerce").fillna(0)
    else:
        q = pd.Series([1] * len(df))
    out["quantity"] = q.astype(float)

    # Entities
    out["from_entity_id"] = df[mapping["from_entity_id"]].astype(str) if "from_entity_id" in mapping else out["supplier_id"]
    out["to_entity_id"] = df[mapping["to_entity_id"]].astype(str) if "to_entity_id" in mapping else out["customer_id"]

    # Geo
    def num_col(canon):
        if canon in mapping:
            return pd.to_numeric(df[mapping[canon]], errors="coerce")
        return pd.Series([np.nan] * len(df))

    out["from_lat"] = num_col("from_lat")
    out["from_lng"] = num_col("from_lng")
    out["to_lat"] = num_col("to_lat")
    out["to_lng"] = num_col("to_lng")

    # Compliance flags (placeholder for future integration with Ledger)
    out["compliance_flags"] = ""

    # Date zone label
    # If datetime exists, set YYYY-MM; else blank
    if out["event_datetime"].notna().any():
        out["date_zone"] = out["event_datetime"].dt.strftime("%Y-%m")
    else:
        out["date_zone"] = ""

    # Ensure all canonical columns exist
    for c in CANON_COLS:
        if c not in out.columns:
            out[c] = "" if c not in ["quantity", "from_lat", "from_lng", "to_lat", "to_lng", "event_datetime"] else np.nan

    # Clean some strings
    for c in ["supplier_id", "customer_id", "license_no", "model_no", "udi", "sn", "lot", "from_entity_id", "to_entity_id"]:
        out[c] = out[c].fillna("").astype(str).str.strip()

    return out[CANON_COLS].copy()

def load_csv(uploaded) -> pd.DataFrame:
    # robust CSV loader: tries utf-8-sig then latin-1
    b = uploaded.getvalue()
    for enc in ["utf-8-sig", "utf-8", "cp950", "latin-1"]:
        try:
            return pd.read_csv(io.BytesIO(b), encoding=enc)
        except Exception:
            continue
    # fallback
    return pd.read_csv(io.BytesIO(b), encoding_errors="ignore")

def generate_sample_dataset(n: int = 800, seed: int = 7) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    suppliers = [f"S{str(i).zfill(3)}" for i in range(1, 13)]
    customers = [f"H{str(i).zfill(3)}" for i in range(1, 25)]
    license_nos = [f"LIC-{i:05d}" for i in range(100, 116)]
    models = [f"M-{i:03d}" for i in range(1, 18)]
    lots = [f"LOT-{i:04d}" for i in range(200, 240)]

    # Taiwan-ish lat/lng
    def rand_loc():
        lat = rng.uniform(21.9, 25.3)
        lng = rng.uniform(120.0, 122.1)
        return lat, lng

    base = dt.datetime.now() - dt.timedelta(days=120)
    rows_d, rows_p = [], []

    for i in range(n):
        sup = rng.choice(suppliers)
        cus = rng.choice(customers)
        lic = rng.choice(license_nos)
        mod = rng.choice(models)
        lot = rng.choice(lots)
        sn = f"SN-{rng.integers(10**7, 10**8-1)}"
        udi = f"(01){rng.integers(10**13, 10**14-1)}(10){lot}(21){sn}"
        qty = int(rng.choice([1, 1, 1, 2, 3, 5]))
        t = base + dt.timedelta(days=int(rng.integers(0, 120)), hours=int(rng.integers(0, 24)))
        from_lat, from_lng = rand_loc()
        to_lat, to_lng = rand_loc()

        rows_d.append({
            "event_datetime": t,
            "supplier_id": sup,
            "customer_id": cus,
            "license_no": lic,
            "model_no": mod,
            "udi": udi,
            "sn": sn,
            "lot": lot,
            "quantity": qty,
            "from_entity_id": sup,
            "to_entity_id": cus,
            "from_lat": from_lat, "from_lng": from_lng,
            "to_lat": to_lat, "to_lng": to_lng,
            "region": rng.choice(["North", "Central", "South", "East", "Islands"]),
            "city": rng.choice(["Taipei", "Taichung", "Tainan", "Kaohsiung", "Hualien"]),
            "postal_code": str(rng.integers(100, 999)),
        })

        # Purchase lags distribution by 0-10 days with noise; occasionally missing (simulating unreported)
        if rng.random() > 0.12:
            lag_days = int(rng.integers(0, 11))
            tp = t + dt.timedelta(days=lag_days, hours=int(rng.integers(0, 12)))
            rows_p.append({
                "event_datetime": tp,
                "supplier_id": sup,           # sometimes hospital records supplier; keep consistent here
                "customer_id": cus,
                "license_no": lic,
                "model_no": mod,
                "udi": udi,
                "sn": sn if rng.random() > 0.05 else "",  # sometimes missing sn
                "lot": lot,
                "quantity": qty,
                "from_entity_id": sup,
                "to_entity_id": cus,
                "from_lat": from_lat, "from_lng": from_lng,
                "to_lat": to_lat, "to_lng": to_lng,
                "region": rng.choice(["North", "Central", "South", "East", "Islands"]),
                "city": rng.choice(["Taipei", "Taichung", "Tainan", "Kaohsiung", "Hualien"]),
                "postal_code": str(rng.integers(100, 999)),
            })

    ddf = pd.DataFrame(rows_d)
    pdf = pd.DataFrame(rows_p)
    return ddf, pdf

def apply_filters(df: pd.DataFrame, filters: Dict[str, Any]) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    out = df.copy()

    # Date range
    start, end = filters.get("date_range", (None, None))
    if start and end and "event_datetime" in out.columns:
        # Convert to naive dt for comparison if needed
        s = pd.to_datetime(start)
        e = pd.to_datetime(end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        mask = out["event_datetime"].notna() & (out["event_datetime"] >= s) & (out["event_datetime"] <= e)
        out = out.loc[mask]

    def multival_filter(col: str, values: List[str]):
        nonlocal out
        if values:
            out = out[out[col].astype(str).isin(values)]

    multival_filter("supplier_id", filters.get("supplier_id", []))
    multival_filter("customer_id", filters.get("customer_id", []))
    multival_filter("license_no", filters.get("license_no", []))
    multival_filter("model_no", filters.get("model_no", []))
    multival_filter("lot", filters.get("lot", []))

    # UDI/SN (batch input)
    udis = filters.get("udi_list", [])
    if udis:
        out = out[out["udi"].astype(str).isin(udis)]

    sns = filters.get("sn_list", [])
    if sns:
        out = out[out["sn"].astype(str).isin(sns)]

    return out

# ----------------------------
# Distribution & Dataset Graphs (6)
# ----------------------------

def graph_gis_chain(df: pd.DataFrame) -> go.Figure:
    if df is None or df.empty:
        fig = go.Figure()
        fig.update_layout(title="GIS Distribution Network Chain (No data)")
        return fig

    # Keep rows with coordinates
    d = df.copy()
    d = d.dropna(subset=["from_lat", "from_lng", "to_lat", "to_lng"], how="any")
    if d.empty:
        fig = go.Figure()
        fig.update_layout(title="GIS Distribution Network Chain (No geo fields)")
        return fig

    # Aggregate by route
    d["route"] = d["from_entity_id"].astype(str) + " → " + d["to_entity_id"].astype(str)
    agg = d.groupby(["from_entity_id", "to_entity_id"], as_index=False).agg(
        qty=("quantity", "sum"),
        from_lat=("from_lat", "mean"),
        from_lng=("from_lng", "mean"),
        to_lat=("to_lat", "mean"),
        to_lng=("to_lng", "mean"),
        n=("quantity", "size"),
    )
    # Limit for performance
    agg = agg.sort_values("qty", ascending=False).head(400)

    # Nodes
    nodes_from = agg[["from_entity_id", "from_lat", "from_lng"]].rename(
        columns={"from_entity_id": "entity_id", "from_lat": "lat", "from_lng": "lng"}
    )
    nodes_to = agg[["to_entity_id", "to_lat", "to_lng"]].rename(
        columns={"to_entity_id": "entity_id", "to_lat": "lat", "to_lng": "lng"}
    )
    nodes = pd.concat([nodes_from, nodes_to], ignore_index=True)
    nodes = nodes.dropna().drop_duplicates(subset=["entity_id"])

    # Build line traces
    fig = go.Figure()

    max_qty = float(agg["qty"].max()) if len(agg) else 1.0

    for _, r in agg.iterrows():
        w = 1 + 8 * (float(r["qty"]) / max_qty)
        fig.add_trace(go.Scattermapbox(
            lat=[r["from_lat"], r["to_lat"]],
            lon=[r["from_lng"], r["to_lng"]],
            mode="lines",
            line=dict(width=w, color="rgba(255,111,97,0.55)"),
            hoverinfo="text",
            text=f"Route: {r['from_entity_id']} → {r['to_entity_id']}<br>Qty: {r['qty']:.0f}<br>Events: {r['n']}",
            showlegend=False,
        ))

    # Add nodes on top
    fig.add_trace(go.Scattermapbox(
        lat=nodes["lat"],
        lon=nodes["lng"],
        mode="markers+text",
        marker=dict(size=10, color="rgba(255,255,255,0.9)"),
        text=nodes["entity_id"],
        textposition="top center",
        hoverinfo="text",
        textfont=dict(size=11),
        showlegend=False,
    ))

    fig.update_layout(
        title="GIS Distribution Network Chain",
        mapbox=dict(style="open-street-map", zoom=6.2, center=dict(lat=23.7, lon=121.0)),
        margin=dict(l=10, r=10, t=50, b=10),
        height=560,
    )
    return fig

def graph_sankey(df: pd.DataFrame) -> go.Figure:
    if df is None or df.empty:
        fig = go.Figure()
        fig.update_layout(title="Supply Chain Sankey (No data)")
        return fig

    d = df.copy()
    # Aggregate flows supplier -> customer
    agg = d.groupby(["supplier_id", "customer_id"], as_index=False)["quantity"].sum()
    agg = agg.sort_values("quantity", ascending=False).head(80)

    labels = pd.Index(pd.concat([agg["supplier_id"], agg["customer_id"]]).unique())
    label_to_idx = {v: i for i, v in enumerate(labels)}

    sources = agg["supplier_id"].map(label_to_idx).tolist()
    targets = agg["customer_id"].map(label_to_idx).tolist()
    values = agg["quantity"].astype(float).tolist()

    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=12, thickness=14,
            line=dict(color="rgba(255,255,255,0.2)", width=1),
            label=labels.tolist(),
        ),
        link=dict(
            source=sources, target=targets, value=values,
            color="rgba(255,111,97,0.35)",
        ),
    )])
    fig.update_layout(title="Supply Chain Sankey (Supplier → Customer)", height=520, margin=dict(l=10, r=10, t=50, b=10))
    return fig

def graph_network(df: pd.DataFrame) -> go.Figure:
    # Simple network scatter using aggregated adjacency; (full force layout requires more libs)
    if df is None or df.empty:
        fig = go.Figure()
        fig.update_layout(title="Network Graph (No data)")
        return fig

    d = df.copy()
    agg = d.groupby(["from_entity_id", "to_entity_id"], as_index=False)["quantity"].sum()
    agg = agg.sort_values("quantity", ascending=False).head(120)

    # Build pseudo-layout using circular placement
    nodes = pd.Index(pd.concat([agg["from_entity_id"], agg["to_entity_id"]]).unique())
    n = len(nodes)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    pos = {nodes[i]: (math.cos(angles[i]), math.sin(angles[i])) for i in range(n)}

    # Edge traces
    edge_x, edge_y, edge_text = [], [], []
    for _, r in agg.iterrows():
        x0, y0 = pos[r["from_entity_id"]]
        x1, y1 = pos[r["to_entity_id"]]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]
        edge_text.append(f"{r['from_entity_id']} → {r['to_entity_id']} | Qty {r['quantity']:.0f}")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=edge_x, y=edge_y,
        mode="lines",
        line=dict(color="rgba(255,111,97,0.35)", width=2),
        hoverinfo="none",
        showlegend=False,
    ))

    # Node sizes by degree
    degree = pd.Series(0, index=nodes)
    for _, r in agg.iterrows():
        degree[r["from_entity_id"]] += 1
        degree[r["to_entity_id"]] += 1

    node_x = [pos[v][0] for v in nodes]
    node_y = [pos[v][1] for v in nodes]
    sizes = (degree.values.astype(float) + 1) * 6

    fig.add_trace(go.Scatter(
        x=node_x, y=node_y,
        mode="markers+text",
        marker=dict(size=sizes, color="rgba(255,255,255,0.92)", line=dict(color="rgba(0,0,0,0.35)", width=1)),
        text=nodes.tolist(),
        textposition="top center",
        hoverinfo="text",
        textfont=dict(size=11),
        showlegend=False,
    ))

    fig.update_layout(
        title="Network Graph (Hub-like view)",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        height=520,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return fig

def graph_time_series_lag(df: pd.DataFrame) -> go.Figure:
    if df is None or df.empty:
        fig = go.Figure()
        fig.update_layout(title="Time Series & Lag (No data)")
        return fig

    d = df.copy()
    d = d[d["event_datetime"].notna()]
    if d.empty:
        fig = go.Figure()
        fig.update_layout(title="Time Series & Lag (No datetime)")
        return fig

    # Timeseries by day and source_dataset
    d["day"] = d["event_datetime"].dt.date
    ts = d.groupby(["day", "source_dataset"], as_index=False)["quantity"].sum()

    fig = px.line(ts, x="day", y="quantity", color="source_dataset", markers=True, title="Time Series (Quantity) by Dataset")
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=50, b=10))

    # Add lag distribution when both datasets exist and SN present
    # Best-effort lag: match by SN (most reliable)
    has_both = set(d["source_dataset"].unique()) >= {"distribution", "purchase"}
    if has_both:
        dd = d[d["source_dataset"] == "distribution"][["sn", "udi", "event_datetime"]].rename(columns={"event_datetime": "t_dist"})
        pp = d[d["source_dataset"] == "purchase"][["sn", "udi", "event_datetime"]].rename(columns={"event_datetime": "t_pur"})
        # Prefer SN join; fallback to UDI if SN missing
        merged = pd.merge(dd[dd["sn"] != ""], pp[pp["sn"] != ""], on="sn", how="inner")
        if merged.empty:
            merged = pd.merge(dd[dd["udi"] != ""], pp[pp["udi"] != ""], on="udi", how="inner")
        if not merged.empty:
            merged["lag_days"] = (merged["t_pur"] - merged["t_dist"]).dt.total_seconds() / 86400.0
            merged = merged[(merged["lag_days"] >= -2) & (merged["lag_days"] <= 60)]
            if not merged.empty:
                hist = px.histogram(merged, x="lag_days", nbins=30, title="Reporting Lag Distribution (days)")
                hist.update_layout(height=280, margin=dict(l=10, r=10, t=50, b=10))
                # Combine via subplot-like approach: show second chart below
                # Streamlit: return as a figure? We'll return main and render hist separately in UI.
                fig._a7_extra_hist = hist  # type: ignore[attr-defined]
    return fig

def graph_heatmap(df: pd.DataFrame) -> go.Figure:
    if df is None or df.empty:
        fig = go.Figure()
        fig.update_layout(title="Heatmap (No data)")
        return fig

    d = df.copy()
    # supplier x customer heatmap of quantity
    pivot = d.pivot_table(
        index="supplier_id",
        columns="customer_id",
        values="quantity",
        aggfunc="sum",
        fill_value=0,
    )
    # limit size for readability
    if pivot.shape[0] > 20:
        top_sup = pivot.sum(axis=1).sort_values(ascending=False).head(20).index
        pivot = pivot.loc[top_sup]
    if pivot.shape[1] > 24:
        top_cus = pivot.sum(axis=0).sort_values(ascending=False).head(24).index
        pivot = pivot[top_cus]

    fig = px.imshow(
        pivot,
        aspect="auto",
        color_continuous_scale="Reds",
        title="Heatmap: Supplier × Customer (Quantity)",
    )
    fig.update_layout(height=520, margin=dict(l=10, r=10, t=50, b=10))
    return fig

def graph_pareto(df: pd.DataFrame) -> go.Figure:
    if df is None or df.empty:
        fig = go.Figure()
        fig.update_layout(title="Pareto (No data)")
        return fig

    d = df.copy()
    agg = d.groupby("supplier_id", as_index=False)["quantity"].sum().sort_values("quantity", ascending=False)
    agg["cum"] = agg["quantity"].cumsum()
    total = float(agg["quantity"].sum()) if len(agg) else 1.0
    agg["cum_pct"] = agg["cum"] / total * 100

    fig = go.Figure()
    fig.add_trace(go.Bar(x=agg["supplier_id"], y=agg["quantity"], name="Quantity", marker_color="rgba(255,111,97,0.75)"))
    fig.add_trace(go.Scatter(x=agg["supplier_id"], y=agg["cum_pct"], name="Cumulative %", yaxis="y2",
                             mode="lines+markers", line=dict(color="rgba(255,255,255,0.85)", width=2)))
    fig.update_layout(
        title="Pareto: Top Suppliers by Quantity",
        yaxis=dict(title="Quantity"),
        yaxis2=dict(title="Cumulative %", overlaying="y", side="right", range=[0, 105]),
        height=520,
        margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h"),
    )
    return fig

# ----------------------------
# AI Ops Dashboard (6 graphs)
# ----------------------------

def ai_ops_figures(runs: List[Dict[str, Any]]) -> Dict[str, go.Figure]:
    if not runs:
        blank = go.Figure().update_layout(title="No runs yet")
        return {k: blank for k in ["calls", "lat", "tokens", "errors", "providers", "outcomes"]}

    df = pd.DataFrame(runs)
    df["ts_dt"] = pd.to_datetime(df["ts"], errors="coerce")
    df = df.sort_values("ts_dt")

    # 1) Calls over time
    calls = df.copy()
    calls["minute"] = calls["ts_dt"].dt.floor("min")
    calls_agg = calls.groupby(["minute", "provider"], as_index=False).size()
    f1 = px.area(calls_agg, x="minute", y="size", color="provider", title="Calls Over Time (per minute)")

    # 2) Latency distribution
    ok = df[df["ok"] == True].copy()
    f2 = px.histogram(ok, x="latency_ms", nbins=30, color="provider", title="Latency Distribution (ms)")

    # 3) Token usage
    tok = ok.copy()
    tok["input_tokens"] = pd.to_numeric(tok["input_tokens"], errors="coerce")
    tok["output_tokens"] = pd.to_numeric(tok["output_tokens"], errors="coerce")
    tok_melt = tok.melt(id_vars=["ts_dt", "provider", "model"], value_vars=["input_tokens", "output_tokens"],
                        var_name="token_type", value_name="tokens")
    tok_melt = tok_melt.dropna(subset=["tokens"])
    f3 = px.bar(tok_melt, x="ts_dt", y="tokens", color="token_type", facet_row="provider",
                title="Token Usage (best-effort where available)")

    # 4) Errors
    err = df[df["ok"] == False].copy()
    if err.empty:
        f4 = go.Figure().update_layout(title="Errors & Retries (No errors)")
    else:
        err["err_type"] = err["error"].fillna("").str.split(":").str[0].replace("", "Unknown")
        err_agg = err.groupby(["err_type"], as_index=False).size().sort_values("size", ascending=False)
        f4 = px.bar(err_agg, x="err_type", y="size", title="Errors by Type")

    # 5) Provider/model share
    share = df.groupby(["provider", "model"], as_index=False).size().sort_values("size", ascending=False)
    f5 = px.treemap(share, path=["provider", "model"], values="size", title="Provider / Model Share")

    # 6) Outcomes proxy (exports, note saves) — session-level; we approximate via logs
    logs = pd.DataFrame(st.session_state.get("live_logs", []))
    if logs.empty:
        f6 = go.Figure().update_layout(title="User Outcomes (No logs)")
    else:
        logs["ts_dt"] = pd.to_datetime(logs["ts"], errors="coerce")
        logs["minute"] = logs["ts_dt"].dt.floor("min")
        # crude outcome tags
        def tag(m: str) -> str:
            m = m.lower()
            if "download" in m or "export" in m:
                return "export"
            if "note" in m and ("saved" in m or "export" in m or "injected" in m):
                return "note"
            if "llm run" in m and "success" in m:
                return "llm_ok"
            if "failed" in m:
                return "error"
            return "other"
        logs["tag"] = logs["msg"].astype(str).map(tag)
        out_agg = logs.groupby(["minute", "tag"], as_index=False).size()
        f6 = px.line(out_agg, x="minute", y="size", color="tag", markers=True, title="User Outcomes (proxy)")

    for f in [f1, f2, f3, f4, f5, f6]:
        f.update_layout(margin=dict(l=10, r=10, t=50, b=10))
    return {"calls": f1, "lat": f2, "tokens": f3, "errors": f4, "providers": f5, "outcomes": f6}

# ----------------------------
# Skill.md & Prompt Lab Module
# ----------------------------

def unified_diff(a: str, b: str, context: int = 3) -> str:
    import difflib
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    diff = difflib.unified_diff(a_lines, b_lines, fromfile="before_skill.md", tofile="after_skill.md", n=context)
    return "".join(diff)

def get_active_llm_config() -> Tuple[Optional[LLMConfig], str]:
    provider = st.session_state.provider
    if provider == "Gemini":
        model = st.session_state.gemini_model
    else:
        model = st.session_state.openai_model

    key, key_source = resolve_api_key(provider)
    if not key:
        return None, "Missing API key"

    cfg = LLMConfig(provider=provider, model=model, api_key=key)
    return cfg, key_source

# ----------------------------
# UI Components
# ----------------------------

def sidebar_settings():
    with st.sidebar:
        st.markdown("## Settings")
        st.session_state.language = st.selectbox("Language / 語言", SUPPORTED_LANGUAGES, index=SUPPORTED_LANGUAGES.index(st.session_state.language))
        st.session_state.theme_mode = st.selectbox("Theme Mode", ["Dark", "Light"], index=0 if st.session_state.theme_mode == "Dark" else 1)

        st.markdown("### Pantone Style")
        style_names = list(PANTONE_STYLES.keys())
        st.session_state.style_name = st.selectbox("Style", style_names, index=style_names.index(st.session_state.style_name))

        st.markdown("### LLM Provider & Model")
        st.session_state.provider = st.selectbox("Provider", SUPPORTED_PROVIDERS, index=SUPPORTED_PROVIDERS.index(st.session_state.provider))

        if st.session_state.provider == "Gemini":
            models = list_models("Gemini")
            st.session_state.gemini_model = st.selectbox("Gemini model", models, index=models.index(st.session_state.gemini_model) if st.session_state.gemini_model in models else 0)
        else:
            models = list_models("OpenAI")
            st.session_state.openai_model = st.selectbox("OpenAI model", models, index=models.index(st.session_state.openai_model) if st.session_state.openai_model in models else 0)

        st.markdown("### API Key")
        st.session_state.override_env_key = st.toggle("Override environment key", value=st.session_state.override_env_key)

        env_key = get_env_key(st.session_state.provider)
        if env_key and not st.session_state.override_env_key:
            st.success("Connected via environment key (hidden).")
        else:
            if st.session_state.provider == "Gemini":
                st.session_state.gemini_api_key_input = st.text_input("GEMINI_API_KEY", type="password", value=st.session_state.gemini_api_key_input)
            else:
                st.session_state.openai_api_key_input = st.text_input("OPENAI_API_KEY", type="password", value=st.session_state.openai_api_key_input)

        if st.button("Test Connection"):
            cfg, src = get_active_llm_config()
            if not cfg:
                st.error("Missing API key.")
            else:
                try:
                    _ = wow_run_llm(
                        "Connection Test",
                        cfg,
                        system="You are a concise assistant.",
                        user="Reply with: OK",
                        show_prompt_expander=False,
                    )
                    st.success(f"OK ({src})")
                except Exception as e:
                    st.error(redact_secrets(str(e)))

        st.markdown("---")
        st.markdown("### Live Log")
        if st.button("Clear logs"):
            st.session_state.live_logs = []
        # show last 12 logs
        logs = st.session_state.live_logs[-12:]
        if logs:
            st.markdown("<div class='a7-log'>", unsafe_allow_html=True)
            for x in logs[::-1]:
                st.markdown(f"`{x['ts']}` **{x['level']}** — {x['msg']}")
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.caption("No logs yet.")

def header_kpis():
    # KPIs based on loaded data + runs
    ddf = st.session_state.distribution_df
    pdf = st.session_state.purchase_df
    udf = st.session_state.unified_df
    runs = st.session_state.runs

    k1 = 0 if ddf is None else len(ddf)
    k2 = 0 if pdf is None else len(pdf)
    k3 = 0 if udf is None else len(udf)
    k4 = len(runs)
    ok = sum(1 for r in runs if r.get("ok"))
    err = k4 - ok

    st.markdown(f"# {APP_TITLE}")
    st.markdown(
        f"<div class='a7-kpi'>"
        f"<div><div class='a7-subtle'>Distribution rows</div><div class='a7-accent'>{k1:,}</div></div>"
        f"<div><div class='a7-subtle'>Purchase rows</div><div class='a7-accent'>{k2:,}</div></div>"
        f"<div><div class='a7-subtle'>Unified events</div><div class='a7-accent'>{k3:,}</div></div>"
        f"<div><div class='a7-subtle'>LLM runs</div><div class='a7-accent'>{k4:,}</div></div>"
        f"<div><div class='a7-subtle'>OK / ERR</div><div class='a7-accent'>{ok:,} / {err:,}</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

# ----------------------------
# Modules
# ----------------------------

def module_data_loader():
    st.markdown("## Data Loader (Distribution / Purchase)")
    st.markdown("<div class='a7-card'>", unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1.2, 1.2, 1.0])
    with c1:
        up_d = st.file_uploader("Upload Distribution dataset (CSV)", type=["csv"], key="up_dist")
    with c2:
        up_p = st.file_uploader("Upload Purchase dataset (CSV)", type=["csv"], key="up_pur")
    with c3:
        if st.button("Load Sample Data"):
            ddf, pdf = generate_sample_dataset()
            st.session_state.distribution_df = ddf
            st.session_state.purchase_df = pdf
            log_event("INFO", "Loaded sample datasets", {"dist_rows": len(ddf), "pur_rows": len(pdf)})

    if up_d is not None:
        try:
            ddf = load_csv(up_d)
            st.session_state.distribution_df = ddf
            log_event("INFO", "Distribution dataset uploaded", {"rows": len(ddf), "cols": len(ddf.columns)})
            st.success(f"Loaded distribution: {len(ddf):,} rows")
        except Exception as e:
            st.error(redact_secrets(str(e)))
            log_event("ERROR", "Failed to load distribution dataset", {"error": str(e)})

    if up_p is not None:
        try:
            pdf = load_csv(up_p)
            st.session_state.purchase_df = pdf
            log_event("INFO", "Purchase dataset uploaded", {"rows": len(pdf), "cols": len(pdf.columns)})
            st.success(f"Loaded purchase: {len(pdf):,} rows")
        except Exception as e:
            st.error(redact_secrets(str(e)))
            log_event("ERROR", "Failed to load purchase dataset", {"error": str(e)})

    st.markdown("<hr class='a7-hr'/>", unsafe_allow_html=True)

    # Unify
    if st.button("Unify into canonical event schema"):
        ddf = st.session_state.distribution_df
        pdf = st.session_state.purchase_df
        frames = []
        if isinstance(ddf, pd.DataFrame) and not ddf.empty:
            frames.append(unify_dataset(ddf, "distribution"))
        if isinstance(pdf, pd.DataFrame) and not pdf.empty:
            frames.append(unify_dataset(pdf, "purchase"))
        if frames:
            udf = pd.concat(frames, ignore_index=True)
            st.session_state.unified_df = udf
            log_event("INFO", "Unified datasets", {"unified_rows": len(udf)})
            st.success(f"Unified events: {len(udf):,} rows")
        else:
            st.warning("No datasets to unify.")

    udf = st.session_state.unified_df
    if isinstance(udf, pd.DataFrame) and not udf.empty:
        st.caption("Unified preview (first 20 rows):")
        st.dataframe(udf.head(20), use_container_width=True, hide_index=True)

        csv_bytes = udf.to_csv(index=False).encode("utf-8")
        if st.download_button("Download unified_events.csv", data=csv_bytes, file_name="unified_events.csv", mime="text/csv"):
            log_event("AUDIT", "Exported unified_events.csv", {"rows": len(udf)})

    st.markdown("</div>", unsafe_allow_html=True)

def module_distribution_dataset():
    st.markdown("## Distribution & Dataset — 6 WOW Interactive Graphs")
    udf = st.session_state.unified_df
    if udf is None or not isinstance(udf, pd.DataFrame) or udf.empty:
        st.info("Load and unify datasets first (Data Loader).")
        return

    st.markdown("<div class='a7-card'>", unsafe_allow_html=True)

    # Dataset selector
    dataset_choice = st.radio("Dataset view", ["distribution", "purchase", "both"], horizontal=True, index=2)
    st.session_state.active_dataset_snapshot = {"dataset_choice": dataset_choice}

    if dataset_choice == "distribution":
        base = udf[udf["source_dataset"] == "distribution"].copy()
    elif dataset_choice == "purchase":
        base = udf[udf["source_dataset"] == "purchase"].copy()
    else:
        base = udf.copy()

    # Filters
    st.markdown("### Filters")
    f1, f2, f3 = st.columns([1.2, 1.2, 1.2])
    min_dt = pd.to_datetime(base["event_datetime"].min(), errors="coerce")
    max_dt = pd.to_datetime(base["event_datetime"].max(), errors="coerce")
    if pd.isna(min_dt) or pd.isna(max_dt):
        min_dt = dt.date.today() - dt.timedelta(days=90)
        max_dt = dt.date.today()

    with f1:
        date_range = st.date_input("Date zone (range)", value=(min_dt.date() if hasattr(min_dt, "date") else min_dt,
                                                              max_dt.date() if hasattr(max_dt, "date") else max_dt))
    with f2:
        supplier_opts = sorted([x for x in base["supplier_id"].dropna().unique().tolist() if str(x).strip() != ""])[:500]
        supplier_id = st.multiselect("Supplier ID", supplier_opts, default=[])
    with f3:
        customer_opts = sorted([x for x in base["customer_id"].dropna().unique().tolist() if str(x).strip() != ""])[:500]
        customer_id = st.multiselect("Customer ID", customer_opts, default=[])

    g1, g2, g3 = st.columns([1.2, 1.2, 1.2])
    with g1:
        license_opts = sorted([x for x in base["license_no"].dropna().unique().tolist() if str(x).strip() != ""])[:500]
        license_no = st.multiselect("license NO", license_opts, default=[])
    with g2:
        model_opts = sorted([x for x in base["model_no"].dropna().unique().tolist() if str(x).strip() != ""])[:500]
        model_no = st.multiselect("model NO", model_opts, default=[])
    with g3:
        lot_opts = sorted([x for x in base["lot"].dropna().unique().tolist() if str(x).strip() != ""])[:500]
        lot = st.multiselect("LOT", lot_opts, default=[])

    s1, s2 = st.columns([1.2, 1.2])
    with s1:
        udi_bulk = st.text_area("UDI (paste multiple; one per line)", height=90, placeholder="(Optional) paste UDI list...")
    with s2:
        sn_bulk = st.text_area("SN (paste multiple; one per line)", height=90, placeholder="(Optional) paste SN list...")

    udi_list = [x.strip() for x in udi_bulk.splitlines() if x.strip()] if udi_bulk else []
    sn_list = [x.strip() for x in sn_bulk.splitlines() if x.strip()] if sn_bulk else []

    filters = {
        "date_range": date_range if isinstance(date_range, tuple) and len(date_range) == 2 else (None, None),
        "supplier_id": supplier_id,
        "customer_id": customer_id,
        "license_no": license_no,
        "model_no": model_no,
        "udi_list": udi_list,
        "sn_list": sn_list,
        "lot": lot,
    }
    st.session_state.active_filter_snapshot = filters

    filtered = apply_filters(base, filters)
    log_event("INFO", "Applied filters (Distribution module)", {"rows": len(filtered), "dataset": dataset_choice})

    st.markdown(f"**Filtered events:** <span class='a7-accent'>{len(filtered):,}</span>", unsafe_allow_html=True)

    st.markdown("<hr class='a7-hr'/>", unsafe_allow_html=True)

    # Graphs
    st.markdown("### Graph 1 — GIS Distribution Network Chain")
    fig1 = graph_gis_chain(filtered)
    st.plotly_chart(fig1, use_container_width=True)

    st.markdown("### Graph 2 — Supply Chain Sankey")
    fig2 = graph_sankey(filtered)
    st.plotly_chart(fig2, use_container_width=True)

    st.markdown("### Graph 3 — Network Graph (Hub-like)")
    fig3 = graph_network(filtered)
    st.plotly_chart(fig3, use_container_width=True)

    st.markdown("### Graph 4 — Time Series & Lag View")
    fig4 = graph_time_series_lag(filtered)
    st.plotly_chart(fig4, use_container_width=True)
    # Render extra histogram if present
    extra = getattr(fig4, "_a7_extra_hist", None)
    if extra is not None:
        st.plotly_chart(extra, use_container_width=True)

    st.markdown("### Graph 5 — Heatmap (Supplier × Customer)")
    fig5 = graph_heatmap(filtered)
    st.plotly_chart(fig5, use_container_width=True)

    st.markdown("### Graph 6 — Pareto (Top Suppliers)")
    fig6 = graph_pareto(filtered)
    st.plotly_chart(fig6, use_container_width=True)

    st.markdown("<hr class='a7-hr'/>", unsafe_allow_html=True)

    # AI Graph Narrator (optional)
    st.markdown("### AI: Graph Narrator (Generate audit-grade insights)")
    cfg, src = get_active_llm_config()
    narrator_prompt = st.text_area(
        "Narrator prompt (editable)",
        value=st.session_state.prompt_library.get("Apply Skill.md to Document (Audit)", ""),
        height=120
    )
    if st.button("Generate Narration (uses filtered aggregates)"):
        if not cfg:
            st.error("Missing API key. Configure in sidebar.")
        else:
            # Provide compact stats (avoid sending large data to LLM)
            stats = {
                "dataset_choice": dataset_choice,
                "filters": {k: (v if len(str(v)) < 1000 else "[TRUNCATED]") for k, v in filters.items()},
                "filtered_rows": int(len(filtered)),
                "top_suppliers": filtered.groupby("supplier_id")["quantity"].sum().sort_values(ascending=False).head(10).to_dict(),
                "top_customers": filtered.groupby("customer_id")["quantity"].sum().sort_values(ascending=False).head(10).to_dict(),
                "top_lots": filtered.groupby("lot")["quantity"].sum().sort_values(ascending=False).head(10).to_dict(),
            }
            system = "You are an audit analyst. Output MUST be Markdown. No hallucinations; only use provided stats."
            user = f"{narrator_prompt}\n\n---\n\nSTATS(JSON):\n{json.dumps(stats, ensure_ascii=False, indent=2)}"
            out = wow_run_llm("Graph Narrator", cfg, system=system, user=user)
            if out:
                st.session_state.note_text = st.session_state.note_text + "\n\n" + out
                log_event("AUDIT", "Injected Graph Narrator output into Note Keeper", {"source": src, "rows": len(filtered)})

    st.markdown("</div>", unsafe_allow_html=True)

def module_ai_ops_dashboard():
    st.markdown("## AI Ops Dashboard (6 graphs)")
    st.markdown("<div class='a7-card'>", unsafe_allow_html=True)

    runs = st.session_state.get("runs", [])
    figs = ai_ops_figures(runs)

    st.plotly_chart(figs["calls"], use_container_width=True)
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(figs["lat"], use_container_width=True)
    with c2:
        st.plotly_chart(figs["errors"], use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        st.plotly_chart(figs["providers"], use_container_width=True)
    with c4:
        st.plotly_chart(figs["outcomes"], use_container_width=True)

    st.plotly_chart(figs["tokens"], use_container_width=True)

    if st.button("Clear run history"):
        st.session_state.runs = []
        log_event("AUDIT", "Cleared AI Ops run history")

    st.markdown("</div>", unsafe_allow_html=True)

def module_note_keeper():
    st.markdown("## AI Note Keeper (Organize / Edit / Download)")
    st.markdown("<div class='a7-card'>", unsafe_allow_html=True)

    cfg, src = get_active_llm_config()

    st.session_state.note_text = st.text_area("Paste note (text or markdown)", value=st.session_state.note_text, height=220)

    prompt_name = st.selectbox("Prompt template", list(st.session_state.prompt_library.keys()),
                              index=list(st.session_state.prompt_library.keys()).index("Note Organizer (Coral Keywords)")
                              if "Note Organizer (Coral Keywords)" in st.session_state.prompt_library else 0)
    note_prompt = st.text_area("Prompt (editable)", value=st.session_state.prompt_library.get(prompt_name, ""), height=140)

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Transform into organized Markdown"):
            if not cfg:
                st.error("Missing API key. Configure in sidebar.")
            else:
                system = "You are a compliance-grade note organizer. Output Markdown only."
                user = f"{note_prompt}\n\n---\n\nNOTE:\n{st.session_state.note_text}"
                out = wow_run_llm("AI Note Keeper: Transform", cfg, system=system, user=user)
                st.session_state.note_output_md = out
                log_event("AUDIT", "Note transformed", {"source": src, "chars_in": len(st.session_state.note_text), "chars_out": len(out)})
    with c2:
        if st.button("Reset output"):
            st.session_state.note_output_md = ""
            log_event("INFO", "Reset note output")

    st.markdown("### Output (Markdown)")
    st.markdown(st.session_state.note_output_md or "_No output yet._", unsafe_allow_html=True)

    # Downloads
    if st.session_state.note_output_md:
        md_bytes = st.session_state.note_output_md.encode("utf-8")
        if st.download_button("Download note_output.md", data=md_bytes, file_name="note_output.md", mime="text/markdown"):
            log_event("AUDIT", "Downloaded note_output.md", {"chars": len(st.session_state.note_output_md)})

    st.markdown("</div>", unsafe_allow_html=True)

def module_skill_prompt_lab():
    st.markdown("## Skill.md & Prompt Lab (Upload / Paste / Improve / Apply)")
    st.markdown("<div class='a7-card'>", unsafe_allow_html=True)

    cfg, src = get_active_llm_config()

    st.markdown("### Part A — Manage skill.md")
    c1, c2 = st.columns([1.0, 1.2])
    with c1:
        up = st.file_uploader("Upload skill.md", type=["md", "txt"], key="upload_skillmd")
        if up is not None:
            try:
                raw = up.getvalue().decode("utf-8", errors="ignore")
                st.session_state.skill_md_text = raw
                log_event("AUDIT", "Uploaded skill.md", {"chars": len(raw)})
                st.success("skill.md loaded into editor.")
            except Exception as e:
                st.error(redact_secrets(str(e)))

    with c2:
        if st.download_button(
            "Download current skill.md",
            data=st.session_state.skill_md_text.encode("utf-8"),
            file_name="skill.md",
            mime="text/markdown"
        ):
            log_event("AUDIT", "Downloaded skill.md", {"chars": len(st.session_state.skill_md_text)})

    st.session_state.skill_md_text = st.text_area("Edit skill.md", value=st.session_state.skill_md_text, height=260)

    st.markdown("### Part B — Improve skill.md with an Agent (default gemini-3.1-flash-lite)")
    prompt_name = st.selectbox("Improver prompt", list(st.session_state.prompt_library.keys()),
                              index=list(st.session_state.prompt_library.keys()).index("Skill Improver (Strict)")
                              if "Skill Improver (Strict)" in st.session_state.prompt_library else 0)
    improver_prompt = st.text_area("Improver prompt (editable)", value=st.session_state.prompt_library[prompt_name], height=160)

    if st.button("Improve skill.md"):
        if not cfg:
            st.error("Missing API key. Configure in sidebar.")
        else:
            before = st.session_state.skill_md_text
            system = "You are a strict skill.md refactoring agent. Output MUST be Markdown only."
            user = f"{improver_prompt}\n\n---\n\nCURRENT skill.md:\n{before}"
            after = wow_run_llm("Skill.md Improver", cfg, system=system, user=user)
            if after:
                # Basic sanity: must start with markdown header
                if not after.lstrip().startswith("#"):
                    st.warning("Improved output did not start with '#'. Keeping but consider re-running with stricter prompt.")
                diff = unified_diff(before, after)
                st.markdown("#### Diff (before → after)")
                st.code(diff[:20000] if diff else "(no diff)", language="diff")
                st.session_state.skill_md_text = after
                log_event("AUDIT", "Improved skill.md", {"source": src, "before_chars": len(before), "after_chars": len(after)})

    st.markdown("<hr class='a7-hr'/>", unsafe_allow_html=True)

    st.markdown("### Part C — Apply skill.md to a Document")
    st.session_state.doc_input_text = st.text_area("Paste document (any text/markdown)", value=st.session_state.doc_input_text, height=220)

    apply_prompt_default = st.session_state.prompt_library.get("Apply Skill.md to Document (Audit)", "")
    apply_prompt = st.text_area("Apply prompt (editable)", value=apply_prompt_default, height=140)

    if st.button("Run: Use skill.md on the document"):
        if not cfg:
            st.error("Missing API key. Configure in sidebar.")
        else:
            system = f"""You are an agent operating under the following skill specification.

<SKILL_MD>
{st.session_state.skill_md_text}
</SKILL_MD>

Hard rules:
- Output Markdown only.
- No hallucinations; only use provided document.
- Redact secrets if any appear.
"""
            user = f"""{apply_prompt}

---
DOCUMENT:
{st.session_state.doc_input_text}
"""
            out = wow_run_llm("Apply skill.md to document", cfg, system=system, user=user)
            st.session_state.doc_output_text = out
            log_event("AUDIT", "Applied skill.md to document", {"source": src, "doc_chars": len(st.session_state.doc_input_text), "out_chars": len(out)})

    st.markdown("### Output (editable)")
    st.session_state.doc_output_text = st.text_area("Result (you can modify before download)", value=st.session_state.doc_output_text, height=240)

    if st.session_state.doc_output_text:
        if st.download_button(
            "Download result.md",
            data=st.session_state.doc_output_text.encode("utf-8"),
            file_name="result.md",
            mime="text/markdown"
        ):
            log_event("AUDIT", "Downloaded result.md", {"chars": len(st.session_state.doc_output_text)})

    st.markdown("</div>", unsafe_allow_html=True)

def module_live_logs_full():
    st.markdown("## Live Logs (Full)")
    st.markdown("<div class='a7-card'>", unsafe_allow_html=True)
    logs = st.session_state.get("live_logs", [])
    if not logs:
        st.caption("No logs yet.")
    else:
        df = pd.DataFrame(logs)
        st.dataframe(df, use_container_width=True, hide_index=True)
        if st.download_button("Download logs.json", data=json.dumps(logs, ensure_ascii=False, indent=2).encode("utf-8"),
                              file_name="aura7_logs.json", mime="application/json"):
            log_event("AUDIT", "Downloaded logs.json", {"entries": len(logs)})
    st.markdown("</div>", unsafe_allow_html=True)

# ----------------------------
# Main App
# ----------------------------

def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")

    ensure_session_defaults()
    inject_css(st.session_state.theme_mode, st.session_state.style_name)

    sidebar_settings()
    header_kpis()

    tabs = st.tabs([
        "1) Data Loader",
        "2) Distribution & Dataset (6 graphs)",
        "3) AI Note Keeper",
        "4) Skill.md & Prompt Lab",
        "5) AI Ops Dashboard (6 graphs)",
        "6) Live Logs",
    ])

    with tabs[0]:
        module_data_loader()

    with tabs[1]:
        module_distribution_dataset()

    with tabs[2]:
        module_note_keeper()

    with tabs[3]:
        module_skill_prompt_lab()

    with tabs[4]:
        module_ai_ops_dashboard()

    with tabs[5]:
        module_live_logs_full()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # last-resort crash shield: show error without leaking secrets
        st.error(f"Fatal error: {type(e).__name__}: {redact_secrets(str(e))}")
        log_event("ERROR", "Fatal error in app", {"error": str(e)})
