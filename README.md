# BPR Indonesian Financial Extractor

Script ini fokus ke **BPR Indonesia** dan dokumen berbahasa Indonesia.

## Input

Dua mode:
1. **Tanpa file input**: pakai daftar default dari `bank_list_bpr.py`.
2. **Pakai Excel**: kolom default `Bank Name` (bisa diubah dengan `--bank-column`).

## Output Excel

Kolom output:
- `Bank Name`
- `Website`
- `Annual Report PDF`
- `Report Year`
- `Currency`
- `Unit Multiplier`
- `Total Assets (IDR)`
- `Total Assets (USD)`
- `Total Loans (IDR)`
- `Total Loans (USD)`
- `Deposits/DPK (IDR)`
- `Deposits/DPK (USD)`
- `FX Rate (IDR per USD)`
- `Extraction Notes`

## Cara kerja (lebih direct ke PDF website)

Untuk tiap bank:
1. Cari kandidat website resmi.
2. Cari kandidat URL PDF langsung dari query `laporan tahunan/publikasi`.
3. Crawl halaman website dengan Playwright (halaman laporan/publikasi/investor/keuangan) lalu ambil semua link `.pdf`.
4. Pilih PDF terbaik (prioritas tahun 2025, lalu 2024).
5. Parse PDF ke Markdown dulu (`pymupdf4llm`) lalu ekstrak istilah Indonesia:
   - Total Aset
   - Kredit/Pinjaman
   - Dana Pihak Ketiga/DPK/Simpanan
6. Deteksi unit (`jutaan`, `miliar`, `triliun`) dan normalisasi IDR→USD.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Jalankan

Pakai default BANK_LIST BPR:

```bash
python bank_financial_extractor.py --output hasil_bpr.xlsx
```

Pakai Excel sendiri:

```bash
python bank_financial_extractor.py --input banks.xlsx --output hasil_bpr.xlsx --bank-column "Bank Name"
```

Set prioritas tahun (misal 2024 dulu):

```bash
python bank_financial_extractor.py --output hasil_bpr.xlsx --years 2024,2025
```

## OCR

Untuk PDF scan/image-based, lihat `OCR_GUIDE.md`.
