#!/usr/bin/env python3
"""Extractor laporan tahunan BPR Indonesia (fokus dokumen berbahasa Indonesia)."""

from __future__ import annotations

import argparse
import asyncio
import io
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

from bank_list_bpr import BANK_LIST

TERM_PATTERNS = {
    "assets": [r"total\s+aset", r"jumlah\s+aset", r"\baset\b"],
    "loans": [r"kredit\s+yang\s+diberikan", r"pinjaman", r"kredit\b"],
    "deposits": [r"dana\s+pihak\s+ketiga", r"\bdpk\b", r"simpanan", r"tabungan"],
}

UNIT_HINTS = {
    "dalam triliun rupiah": 1_000_000_000_000,
    "triliun": 1_000_000_000_000,
    "dalam miliar rupiah": 1_000_000_000,
    "miliar": 1_000_000_000,
    "dalam jutaan rupiah": 1_000_000,
    "jutaan": 1_000_000,
    "juta": 1_000_000,
    "dalam ribuan rupiah": 1_000,
    "ribuan": 1_000,
}

SOCIAL_HOSTS = {"facebook.com", "instagram.com", "linkedin.com", "youtube.com", "tiktok.com", "x.com", "twitter.com"}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def get_live_fx_rate_idr_per_usd(default_rate: float = 16_000.0) -> float:
    urls = [
        "https://api.exchangerate.host/latest?base=USD&symbols=IDR",
        "https://open.er-api.com/v6/latest/USD",
    ]
    for url in urls:
        try:
            res = requests.get(url, timeout=20)
            res.raise_for_status()
            data = res.json()
            if isinstance(data, dict) and "rates" in data and "IDR" in data["rates"]:
                return float(data["rates"]["IDR"])
        except Exception:
            continue
    return default_rate


def extract_links_from_html(html: str) -> List[str]:
    return re.findall(r'href=["\'](https?://[^"\']+)["\']', html, flags=re.I)


def ddg_search(query: str) -> List[str]:
    try:
        res = requests.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        res.raise_for_status()
        return extract_links_from_html(res.text)
    except Exception:
        return []


def guess_websites(bank_name: str) -> List[str]:
    queries = [
        f'"{bank_name}" "situs resmi"',
        f'"{bank_name}" "profil"',
        f'"{bank_name}" "laporan publikasi"',
    ]
    links: List[str] = []
    for q in queries:
        links.extend(ddg_search(q))

    cleaned = []
    for link in links:
        host = urlparse(link).netloc.lower().replace("www.", "")
        if any(h in host for h in SOCIAL_HOSTS):
            continue
        cleaned.append(link)

    def score(u: str) -> int:
        lu = u.lower()
        s = 0
        if "investor" in lu or "hubungan-investor" in lu:
            s += 5
        if "bpr" in lu:
            s += 3
        if "ojk" in lu:
            s -= 2
        return s

    ranked = sorted(dict.fromkeys(cleaned), key=score, reverse=True)
    return ranked[:5]


def search_pdf_urls(bank_name: str, years: Sequence[int]) -> List[str]:
    queries = [
        f'"{bank_name}" "laporan tahunan" filetype:pdf',
        f'"{bank_name}" "laporan publikasi" filetype:pdf',
        f'"{bank_name}" "annual report" filetype:pdf',
    ]
    links: List[str] = []
    for query in queries:
        links.extend([u for u in ddg_search(query) if ".pdf" in u.lower()])

    ranked = []
    for link in dict.fromkeys(links):
        lower = link.lower()
        s = 0
        if any(str(y) in lower for y in years):
            s += 4
        if any(k in lower for k in ["laporan-tahunan", "annual", "tahunan", "publikasi"]):
            s += 2
        ranked.append((s, link))
    ranked.sort(reverse=True)
    return [u for _, u in ranked[:20]]


async def crawl_site_for_pdfs(start_url: str, years: Sequence[int], max_pages: int = 15) -> List[str]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []

    pdfs: List[str] = []
    seen: set[str] = set()
    to_visit: List[str] = [start_url]
    base_host = urlparse(start_url).netloc

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0")
        page = await context.new_page()

        while to_visit and len(seen) < max_pages:
            url = to_visit.pop(0)
            if url in seen:
                continue
            seen.add(url)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
                html = await page.content()
            except Exception:
                continue

            raw_links = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)
            for href in raw_links:
                full = urljoin(url, href)
                low = full.lower()
                if ".pdf" in low:
                    pdfs.append(full)
                    continue
                if urlparse(full).netloc != base_host:
                    continue
                if any(k in low for k in ["laporan", "publikasi", "annual", "investor", "keuangan", "transparansi"]):
                    if full not in seen and full not in to_visit:
                        to_visit.append(full)

        await browser.close()

    ranked = []
    for link in dict.fromkeys(pdfs):
        low = link.lower()
        score = 0
        if any(str(y) in low for y in years):
            score += 4
        if any(k in low for k in ["laporan", "annual", "tahunan", "publikasi"]):
            score += 2
        ranked.append((score, link))
    ranked.sort(reverse=True)
    return [u for _, u in ranked]


def pdf_to_markdown(pdf_bytes: bytes) -> str:
    try:
        import pymupdf4llm

        return pymupdf4llm.to_markdown(io.BytesIO(pdf_bytes))
    except Exception:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return "\n".join(page.get_text("text") for page in doc)


def parse_number(raw: str) -> Optional[float]:
    token = raw.strip().replace(" ", "")
    if not token:
        return None
    if "," in token and "." in token:
        token = token.replace(".", "").replace(",", ".")
    elif token.count(".") > 1:
        token = token.replace(".", "")
    elif token.count(",") > 1:
        token = token.replace(",", "")
    else:
        token = token.replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None


def detect_currency_and_multiplier(text: str) -> Tuple[str, float]:
    sample = normalize_text(text[:80_000])
    currency = "IDR" if any(k in sample for k in ["rupiah", "rp", "idr"]) else "UNKNOWN"
    mult = 1.0
    for key, value in UNIT_HINTS.items():
        if key in sample:
            mult = max(mult, float(value))
    return currency, mult


def extract_metric_value(text: str, metric_key: str) -> Tuple[Optional[float], str]:
    patterns = TERM_PATTERNS[metric_key]
    lines = text.splitlines()
    best: Tuple[Optional[float], str] = (None, "")
    candidates: List[Tuple[float, str]] = []

    for i, line in enumerate(lines):
        norm = normalize_text(line)
        if any(re.search(p, norm) for p in patterns):
            local = " ".join(lines[i : min(i + 3, len(lines))])
            nums = re.findall(r"[-+]?\d[\d\.,]{2,}", local)
            for n in nums:
                val = parse_number(n)
                if val is not None:
                    candidates.append((val, local[:220]))

    if candidates:
        best = sorted(candidates, key=lambda x: x[0], reverse=True)[0]
    return best


def idr_to_usd(value: Optional[float], currency: str, multiplier: float, fx_idr_per_usd: float) -> Tuple[Optional[float], Optional[float]]:
    if value is None:
        return None, None
    if currency == "IDR":
        idr_value = value * multiplier
        return idr_value, idr_value / fx_idr_per_usd
    return value, value


def pick_best_pdf(candidates: Iterable[str], years: Sequence[int]) -> Optional[str]:
    ranked = []
    for link in dict.fromkeys(candidates):
        low = link.lower()
        s = 0
        if any(str(y) in low for y in years):
            s += 5
        if any(k in low for k in ["laporan", "annual", "tahunan", "publikasi"]):
            s += 3
        if "ojk" in low:
            s -= 1
        ranked.append((s, link))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][1]


def process_bank(bank_name: str, fx_rate: float, years: Sequence[int]) -> Dict[str, object]:
    row: Dict[str, object] = {
        "Bank Name": bank_name,
        "Website": None,
        "Annual Report PDF": None,
        "Report Year": None,
        "Currency": None,
        "Unit Multiplier": None,
        "Total Assets (IDR)": None,
        "Total Assets (USD)": None,
        "Total Loans (IDR)": None,
        "Total Loans (USD)": None,
        "Deposits/DPK (IDR)": None,
        "Deposits/DPK (USD)": None,
        "FX Rate (IDR per USD)": fx_rate,
        "Extraction Notes": "",
    }

    sites = guess_websites(bank_name)
    if sites:
        row["Website"] = sites[0]

    pdf_candidates = search_pdf_urls(bank_name, years)
    for site in sites[:3]:
        crawled = asyncio.run(crawl_site_for_pdfs(site, years))
        pdf_candidates.extend(crawled)

    best_pdf = pick_best_pdf(pdf_candidates, years)
    if not best_pdf:
        row["Extraction Notes"] = "PDF laporan tahunan/publikasi tidak ditemukan"
        return row

    row["Annual Report PDF"] = best_pdf
    for y in years:
        if str(y) in best_pdf:
            row["Report Year"] = y
            break

    try:
        pdf = requests.get(best_pdf, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        pdf.raise_for_status()
        text = pdf_to_markdown(pdf.content)
    except Exception as exc:
        row["Extraction Notes"] = f"Gagal download/parse PDF: {exc}"
        return row

    currency, multiplier = detect_currency_and_multiplier(text)
    row["Currency"] = currency
    row["Unit Multiplier"] = multiplier

    aset, aset_evd = extract_metric_value(text, "assets")
    kredit, kredit_evd = extract_metric_value(text, "loans")
    dpk, dpk_evd = extract_metric_value(text, "deposits")

    aset_idr, aset_usd = idr_to_usd(aset, currency, multiplier, fx_rate)
    kredit_idr, kredit_usd = idr_to_usd(kredit, currency, multiplier, fx_rate)
    dpk_idr, dpk_usd = idr_to_usd(dpk, currency, multiplier, fx_rate)

    row["Total Assets (IDR)"] = aset_idr
    row["Total Assets (USD)"] = aset_usd
    row["Total Loans (IDR)"] = kredit_idr
    row["Total Loans (USD)"] = kredit_usd
    row["Deposits/DPK (IDR)"] = dpk_idr
    row["Deposits/DPK (USD)"] = dpk_usd

    row["Extraction Notes"] = (
        f"aset: {aset_evd or 'tidak ditemukan'} | "
        f"kredit: {kredit_evd or 'tidak ditemukan'} | "
        f"dpk: {dpk_evd or 'tidak ditemukan'}"
    )
    return row


def load_banks(input_excel: Optional[Path], bank_column: str) -> List[str]:
    if input_excel:
        df = pd.read_excel(input_excel)
        if bank_column not in df.columns:
            raise ValueError(f"Kolom tidak ada: {bank_column}")
        return [b for b in df[bank_column].dropna().astype(str).tolist() if b.strip()]
    return BANK_LIST


def run(input_excel: Optional[Path], output_excel: Path, bank_column: str, years: Sequence[int]) -> None:
    banks = load_banks(input_excel, bank_column)
    fx_rate = get_live_fx_rate_idr_per_usd()

    rows = []
    for idx, bank in enumerate(banks, start=1):
        print(f"[{idx}/{len(banks)}] Proses: {bank}")
        rows.append(process_bank(bank, fx_rate, years))

    out_df = pd.DataFrame(rows)
    out_df.to_excel(output_excel, index=False)
    print(f"Selesai. File output: {output_excel}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extractor data finansial BPR dari PDF laporan tahunan/publikasi")
    p.add_argument("--input", type=Path, default=None, help="Excel input opsional (kalau tidak diisi, pakai BANK_LIST bawaan)")
    p.add_argument("--output", type=Path, required=True, help="Excel output")
    p.add_argument("--bank-column", default="Bank Name", help="Nama kolom bank pada file input")
    p.add_argument("--years", default="2025,2024", help="Prioritas tahun, contoh: 2025,2024")
    return p.parse_args(argv)


def main() -> int:
    args = parse_args()
    years = [int(x.strip()) for x in args.years.split(",") if x.strip()]
    run(args.input, args.output, args.bank_column, years)
    return 0


if __name__ == "__main__":
    sys.exit(main())
